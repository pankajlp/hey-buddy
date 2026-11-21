"""
Hinglish (Hindi written in Roman letters) for speech.

Voice models read Roman-letter Hindi with English sounds ("aaj" -> "arrest"). So Hinglish is first
rewritten in Devanagari by a local Ollama model, then spoken by a Hindi-capable voice.

  looks_hinglish(text)            cheap word-list check, no model call
  to_devanagari(text, host, model) the rewrite (cached), or None if Ollama is unavailable

Self-contained (only httpx), so the same file can live in more than one project.
"""
import re
import threading
from collections import OrderedDict
from typing import Optional

import httpx

DEVANAGARI = re.compile(r"[ऀ-ॿ]")
_WORD = re.compile(r"[A-Za-z]+")

# Common Roman-letter Hindi words. Words that are also everyday English ("to", "me", "main",
# "par", "hi", "log", "sun", "mat", "the") are left out, so English text isn't mistaken for Hinglish.
HINGLISH_WORDS = set("""
hai hain ho hoon hun tha thi thay hoga hogi honge hua hui hue hota hoti hote
kya kyu kyun kyon kyunki kaise kaisa kaisi kab kahan kaha kidhar kaun kis kisi kisko kitna kitne kitni
nahi nahin nhi haan han ji accha acha achha theek thik bilkul zaroor jarur shayad sach sahi galat
mein mujhe mujhko mera meri mere tum tumhe tumko tumhara tumhari tera teri tere tujhe tujhko tu
aap aapka aapki aapke aapko apna apni apne hum hame humein humko hamara hamari humara
yeh ye woh wo voh isko usko unko inko iska uska unka inka yahan wahan idhar udhar
aur bhi sirf bas lekin magar phir fir ya agar toh
ka ki ke ko se ne pe tak wala wali wale
arre yaar yar bhai bhaiya didi dost
aaj kal abhi kabhi hamesha pehle baad jaldi der raat subah shaam dopahar
bahut bohot bahot thoda thodi zyada jyada kam kaam ghar khana paani chai pyaar baat baatein
karo karna karunga karungi karenge karta karti karte kiya kiye kar raha rahi rahe
chalo chal chalein chalte chale jao jaana jana aao aana aaya aayi gaya gayi
dekho dekh dekhna dekha bolo bol bola bata batao bataiye suno samajh samjha pata
khao piyo socho ruko ruk raho rehna rehta rehti milte milenge mila mili
chahiye chahta chahti sakta sakti sakte diya diye liya liye deta deti dete leta leti lete
haal kuch koi sab sabko mujhse tumse aapse unse usse isse
""".split())

FEW_SHOT = [
    ("Arre yaar, aaj office mein bahut kaam tha.", "अरे यार, आज ऑफिस में बहुत काम था।"),
    ("Tum kaise ho? Kal meeting hai na?", "तुम कैसे हो? कल मीटिंग है ना?"),
    ("Hello! This is Ravi checking the new phone. Kal milte hain, theek hai?",
     "हेलो! दिस इज़ रवि चेकिंग द न्यू फ़ोन। कल मिलते हैं, ठीक है?"),
]
SYSTEM = (
    "Rewrite the user's Hinglish (Hindi written in Roman letters) in Devanagari script, exactly as "
    "it would be spoken. This is a change of script only: keep every word, in the same order. Never "
    "translate, answer, add or drop words. English words and whole English sentences stay English, "
    "just written in Devanagari letters as they sound (office -> ऑफिस, this is -> दिस इज़). "
    "Numbers stay as digits. Output only the Devanagari text."
)

_cache: "OrderedDict[str, str]" = OrderedDict()
_cache_lock = threading.Lock()


def looks_hinglish(text: str) -> bool:
    """True when most Roman-letter words are Hindi (needs at least two Hindi words, or a short
    phrase made only of them). Text already in Devanagari is not Hinglish."""
    if not text or DEVANAGARI.search(text):
        return False
    words = [w.lower() for w in _WORD.findall(text)]
    if not words:
        return False
    hits = sum(w in HINGLISH_WORDS for w in words)
    if len(words) <= 3:
        return hits >= 2 or (hits == len(words) and hits >= 1 and len(words) > 1)
    return hits >= 2 and hits / len(words) >= 0.3


def to_devanagari(text: str, host: str, model: str, timeout: float = 30.0) -> Optional[str]:
    """Hinglish -> Devanagari with a local Ollama model, or None if that fails."""
    text = text.strip()
    if not text:
        return None
    with _cache_lock:
        if text in _cache:
            _cache.move_to_end(text)
            return _cache[text]
    messages = [{"role": "system", "content": SYSTEM}]
    for src, dst in FEW_SHOT:
        messages += [{"role": "user", "content": src}, {"role": "assistant", "content": dst}]
    messages.append({"role": "user", "content": text})
    body = {"model": model, "messages": messages, "stream": False,
            "options": {"temperature": 0, "num_predict": max(64, len(text) * 3)}}
    try:
        r = httpx.post(f"{host}/api/chat", json={**body, "think": False}, timeout=timeout)
        if r.status_code == 400 and "think" in r.text:   # model without a thinking switch
            r = httpx.post(f"{host}/api/chat", json=body, timeout=timeout)
        r.raise_for_status()
        out = r.json().get("message", {}).get("content", "")
    except Exception:
        return None
    out = re.sub(r"<think>.*?</think>", "", out, flags=re.S).strip().strip('"“”')
    # Sanity: mostly Devanagari, and not an essay instead of a rewrite.
    letters = re.findall(r"[A-Za-zऀ-ॿ]", out)
    if not letters or sum(bool(DEVANAGARI.match(c)) for c in letters) / len(letters) < 0.6:
        return None
    if len(out) > len(text) * 3 + 20:
        return None
    with _cache_lock:
        _cache[text] = out
        if len(_cache) > 500:
            _cache.popitem(last=False)
    return out
