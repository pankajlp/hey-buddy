import Foundation
import Cocoa
import CoreGraphics
import Vision

let args = CommandLine.arguments
if args.count < 2 {
    let help = """
    Usage: buddy_helper <command> [args...]
    Commands:
      app_info               Get frontmost app and screen resolution
      running_apps           Get list of active GUI applications
      mouse_pos              Get current cursor position (x, y)
      mouse_move <x> <y>     Move mouse to (x, y)
      mouse_click <x> <y> [left|right|double]
      ocr_image <path>       Extract text and bounding boxes from an image file
      web_elements <bundle_id> [filter]   List links, buttons, and fields on the front page
      web_press <bundle_id> <name>        Click the page link/button with this name
      web_focus <bundle_id> <name>        Focus the page text field with this name
      web_text <bundle_id>                Title and visible text of the front page
      web_links <bundle_id> <url_part>    Links on the front page whose address contains url_part
      music_play_album [timeout_s] [track_title]
                             Press Play on the album page open in Music (once it shows track_title)
    """
    print(help)
    exit(0)
}

let cmd = args[1]

func outputJSON(_ object: Any) {
    if let data = try? JSONSerialization.data(withJSONObject: object, options: [.prettyPrinted, .sortedKeys]),
       let str = String(data: data, encoding: .utf8) {
        print(str)
    } else {
        print("{\"error\": \"Failed to serialize JSON\"}")
    }
}


// ---- Accessibility helpers (for driving the Music app without screen coordinates) ----
func axAttr(_ e: AXUIElement, _ a: String) -> AnyObject? {
    var v: AnyObject?
    AXUIElementCopyAttributeValue(e, a as CFString, &v)
    return v
}
func axChildren(_ e: AXUIElement) -> [AXUIElement] { (axAttr(e, kAXChildrenAttribute) as? [AXUIElement]) ?? [] }
func axRole(_ e: AXUIElement) -> String { axAttr(e, kAXRoleAttribute) as? String ?? "" }
func axLabels(_ e: AXUIElement) -> [String] {
    [axAttr(e, kAXTitleAttribute), axAttr(e, kAXDescriptionAttribute), axAttr(e, kAXValueAttribute)]
        .compactMap { $0 as? String }.filter { !$0.isEmpty }
}
func axFind(_ e: AXUIElement, depth: Int = 0, _ match: (AXUIElement) -> Bool) -> AXUIElement? {
    if depth > 40 { return nil }
    if match(e) { return e }
    for c in axChildren(e) { if let f = axFind(c, depth: depth + 1, match) { return f } }
    return nil
}
func normalized(_ s: String) -> String {
    s.lowercased().components(separatedBy: CharacterSet.alphanumerics.inverted).filter { !$0.isEmpty }.joined(separator: " ")
}


// ---- Web pages (Chrome, Safari) through accessibility ----
let webClickRoles: Set<String> = ["AXLink", "AXButton", "AXTab", "AXMenuItem", "AXCheckBox", "AXRadioButton",
                                  "AXPopUpButton", "AXMenuButton"]
let webFieldRoles: Set<String> = ["AXTextField", "AXSearchField", "AXComboBox", "AXTextArea"]

func webAreas(_ bundle: String) -> [AXUIElement]? {
    guard let running = NSRunningApplication.runningApplications(withBundleIdentifier: bundle).first else { return nil }
    let app = AXUIElementCreateApplication(running.processIdentifier)
    // Chromium only builds the page's accessibility tree once asked to
    AXUIElementSetAttributeValue(app, "AXManualAccessibility" as CFString, kCFBooleanTrue)
    for attempt in 0..<8 {
        var areas: [AXUIElement] = []
        if let w = axAttr(app, kAXFocusedWindowAttribute) ?? axAttr(app, kAXMainWindowAttribute) {
            func find(_ e: AXUIElement, _ d: Int) {
                if d > 40 { return }
                if axRole(e) == "AXWebArea" { areas.append(e); return }
                for c in axChildren(e) { find(c, d + 1) }
            }
            find(w as! AXUIElement, 0)
        }
        if !areas.isEmpty && (attempt > 0 || !axChildren(areas[0]).isEmpty) { return areas }
        usleep(250000)
    }
    return []
}

func webName(_ e: AXUIElement) -> String {
    for a in [kAXTitleAttribute, kAXDescriptionAttribute, "AXPlaceholderValue", kAXHelpAttribute] {
        if let s = axAttr(e, a) as? String, !s.trimmingCharacters(in: .whitespaces).isEmpty { return s }
    }
    // Links often carry their text in child static text
    var parts: [String] = []
    func collect(_ x: AXUIElement, _ d: Int) {
        if d > 6 || parts.joined().count > 100 { return }
        if axRole(x) == "AXStaticText", let v = axAttr(x, kAXValueAttribute) as? String { parts.append(v) }
        for c in axChildren(x) { collect(c, d + 1) }
    }
    collect(e, 0)
    return parts.joined(separator: " ").trimmingCharacters(in: .whitespacesAndNewlines)
}

func webCollect(_ areas: [AXUIElement], roles: Set<String>) -> [(AXUIElement, String, String)] {
    var out: [(AXUIElement, String, String)] = []
    var count = 0
    func walk(_ e: AXUIElement, _ d: Int) {
        if d > 80 || count > 30000 { return }
        count += 1
        let role = axRole(e)
        if roles.contains(role) {
            let name = webName(e).replacingOccurrences(of: "\n", with: " ")
            if !name.isEmpty { out.append((e, role, String(name.prefix(120)))) }
            if role == "AXLink" || role == "AXButton" { return }  // don't list parts of a link separately
        }
        for c in axChildren(e) { walk(c, d + 1) }
    }
    areas.forEach { walk($0, 0) }
    return out
}

func webBestMatch(_ items: [(AXUIElement, String, String)], _ query: String) -> (AXUIElement, String, String)? {
    let q = normalized(query)
    if q.isEmpty { return nil }
    let named = items.map { ($0, normalized($0.2)) }
    if let exact = named.first(where: { $0.1 == q }) { return exact.0 }
    let starts = named.filter { $0.1.hasPrefix(q) }.sorted { $0.1.count < $1.1.count }
    if let s = starts.first { return s.0 }
    let contains = named.filter { $0.1.contains(q) }.sorted { $0.1.count < $1.1.count }
    return contains.first?.0
}

func shortRole(_ role: String) -> String {
    ["AXLink": "link", "AXButton": "button", "AXTab": "tab", "AXMenuItem": "menu item", "AXCheckBox": "checkbox",
     "AXRadioButton": "option", "AXPopUpButton": "menu", "AXMenuButton": "menu", "AXTextField": "field",
     "AXSearchField": "search field", "AXComboBox": "field", "AXTextArea": "text box"][role] ?? role
}

switch cmd {
case "app_info":
    let app = NSWorkspace.shared.frontmostApplication
    let mainScreen = NSScreen.main
    let frame = mainScreen?.frame ?? .zero
    let vis = mainScreen?.visibleFrame ?? frame
    let mouseLoc = NSEvent.mouseLocation
    // Convert mouse y from Cocoa (bottom-left) to CoreGraphics (top-left)
    let screenH = frame.height > 0 ? frame.height : 1080.0
    let mouseX = mouseLoc.x
    let mouseY = screenH - mouseLoc.y
    
    let info: [String: Any] = [
        "app_name": app?.localizedName ?? "Unknown",
        "bundle_id": app?.bundleIdentifier ?? "",
        "pid": app?.processIdentifier ?? 0,
        "screen_width": frame.width,
        "screen_height": frame.height,
        "mouse_x": Int(mouseX),
        "mouse_y": Int(mouseY),
        // Usable area without menu bar and Dock, in top-left coordinates
        "visible_x": Int(vis.origin.x),
        "visible_y": Int(screenH - vis.origin.y - vis.height),
        "visible_width": Int(vis.width),
        "visible_height": Int(vis.height)
    ]
    outputJSON(info)

case "running_apps":
    let apps = NSWorkspace.shared.runningApplications
    let guiApps = apps.filter { $0.activationPolicy == .regular }
    let list: [[String: Any]] = guiApps.compactMap { app in
        guard let name = app.localizedName, !name.isEmpty else { return nil }
        return [
            "name": name,
            "bundle_id": app.bundleIdentifier ?? "",
            "pid": app.processIdentifier,
            "is_active": app.isActive
        ]
    }
    outputJSON(["count": list.count, "apps": list])

case "mouse_pos":
    let mouseLoc = NSEvent.mouseLocation
    let screenH = NSScreen.main?.frame.height ?? 1080.0
    let x = Int(mouseLoc.x)
    let y = Int(screenH - mouseLoc.y)
    outputJSON(["x": x, "y": y])

case "mouse_move":
    guard args.count >= 4, let x = Double(args[2]), let y = Double(args[3]) else {
        outputJSON(["error": "Usage: mouse_move <x> <y>"])
        exit(1)
    }
    let point = CGPoint(x: x, y: y)
    let move = CGEvent(mouseEventSource: nil, mouseType: .mouseMoved, mouseCursorPosition: point, mouseButton: .left)
    move?.post(tap: .cghidEventTap)
    outputJSON(["status": "ok", "action": "mouse_move", "x": x, "y": y])

case "mouse_click":
    guard args.count >= 4, let x = Double(args[2]), let y = Double(args[3]) else {
        outputJSON(["error": "Usage: mouse_click <x> <y> [left|right|double]"])
        exit(1)
    }
    let clickType = args.count > 4 ? args[4].lowercased() : "left"
    let point = CGPoint(x: x, y: y)
    
    // First move to position
    let move = CGEvent(mouseEventSource: nil, mouseType: .mouseMoved, mouseCursorPosition: point, mouseButton: .left)
    move?.post(tap: .cghidEventTap)
    usleep(20000)
    
    if clickType == "right" {
        let down = CGEvent(mouseEventSource: nil, mouseType: .rightMouseDown, mouseCursorPosition: point, mouseButton: .right)
        let up = CGEvent(mouseEventSource: nil, mouseType: .rightMouseUp, mouseCursorPosition: point, mouseButton: .right)
        down?.post(tap: .cghidEventTap)
        usleep(40000)
        up?.post(tap: .cghidEventTap)
    } else if clickType == "double" {
        let down1 = CGEvent(mouseEventSource: nil, mouseType: .leftMouseDown, mouseCursorPosition: point, mouseButton: .left)
        down1?.setIntegerValueField(.mouseEventClickState, value: 1)
        let up1 = CGEvent(mouseEventSource: nil, mouseType: .leftMouseUp, mouseCursorPosition: point, mouseButton: .left)
        up1?.setIntegerValueField(.mouseEventClickState, value: 1)
        
        let down2 = CGEvent(mouseEventSource: nil, mouseType: .leftMouseDown, mouseCursorPosition: point, mouseButton: .left)
        down2?.setIntegerValueField(.mouseEventClickState, value: 2)
        let up2 = CGEvent(mouseEventSource: nil, mouseType: .leftMouseUp, mouseCursorPosition: point, mouseButton: .left)
        up2?.setIntegerValueField(.mouseEventClickState, value: 2)
        
        down1?.post(tap: .cghidEventTap)
        up1?.post(tap: .cghidEventTap)
        usleep(40000)
        down2?.post(tap: .cghidEventTap)
        up2?.post(tap: .cghidEventTap)
    } else {
        let down = CGEvent(mouseEventSource: nil, mouseType: .leftMouseDown, mouseCursorPosition: point, mouseButton: .left)
        let up = CGEvent(mouseEventSource: nil, mouseType: .leftMouseUp, mouseCursorPosition: point, mouseButton: .left)
        down?.post(tap: .cghidEventTap)
        usleep(40000)
        up?.post(tap: .cghidEventTap)
    }
    outputJSON(["status": "ok", "action": "mouse_click", "x": x, "y": y, "type": clickType])

case "ocr_image":
    guard args.count >= 3 else {
        outputJSON(["error": "Usage: ocr_image <image_path>"])
        exit(1)
    }
    let imagePath = args[2]
    guard let img = NSImage(contentsOfFile: imagePath),
          let cgImg = img.cgImage(forProposedRect: nil, context: nil, hints: nil) else {
        outputJSON(["error": "Failed to load image at \(imagePath)"])
        exit(1)
    }
    
    let width = CGFloat(cgImg.width)
    let height = CGFloat(cgImg.height)
    
    let request = VNRecognizeTextRequest()
    request.recognitionLevel = .accurate
    request.usesLanguageCorrection = true
    
    let handler = VNImageRequestHandler(cgImage: cgImg, options: [:])
    do {
        try handler.perform([request])
    } catch {
        outputJSON(["error": "OCR failed: \(error.localizedDescription)"])
        exit(1)
    }
    
    var items: [[String: Any]] = []
    if let observations = request.results {
        for obs in observations {
            if let top = obs.topCandidates(1).first {
                let bbox = obs.boundingBox
                let x = Int(bbox.origin.x * width)
                let y = Int((1.0 - bbox.origin.y - bbox.height) * height)
                let w = Int(bbox.width * width)
                let h = Int(bbox.height * height)
                let cx = x + (w / 2)
                let cy = y + (h / 2)
                
                items.append([
                    "text": top.string,
                    "confidence": Double(round(100 * top.confidence) / 100),
                    "x": x,
                    "y": y,
                    "width": w,
                    "height": h,
                    "center_x": cx,
                    "center_y": cy
                ])
            }
        }
    }
    outputJSON(["image_width": Int(width), "image_height": Int(height), "count": items.count, "elements": items])

case "web_elements":
    guard args.count >= 3, let areas = webAreas(args[2]) else {
        outputJSON(["error": "Browser is not running"]); exit(1)
    }
    let filter = args.count > 3 ? normalized(args[3]) : ""
    var seen = Set<String>()
    var list: [[String: String]] = []
    for (_, role, name) in webCollect(areas, roles: webClickRoles.union(webFieldRoles)) {
        let key = role + "|" + name
        if seen.contains(key) || (!filter.isEmpty && !normalized(name).contains(filter)) { continue }
        seen.insert(key)
        list.append(["type": shortRole(role), "name": name])
        if list.count >= 250 { break }
    }
    let title = areas.first.flatMap { axAttr($0, kAXTitleAttribute) as? String ?? axAttr($0, kAXDescriptionAttribute) as? String } ?? ""
    outputJSON(["page": title, "elements": list])

case "web_press":
    guard args.count >= 4, let areas = webAreas(args[2]) else {
        outputJSON(["error": "Usage: web_press <bundle_id> <name> (and the browser must be running)"]); exit(1)
    }
    guard let (el, role, name) = webBestMatch(webCollect(areas, roles: webClickRoles), args[3]) else {
        outputJSON(["error": "No link or button named '\(args[3])' on the page"]); exit(1)
    }
    let r = AXUIElementPerformAction(el, kAXPressAction as CFString)
    if r == .success { outputJSON(["status": "ok", "clicked": name, "type": shortRole(role)]) }
    else { outputJSON(["error": "Couldn't click '\(name)' (error \(r.rawValue))"]); exit(1) }

case "web_focus":
    guard args.count >= 4, let areas = webAreas(args[2]) else {
        outputJSON(["error": "Usage: web_focus <bundle_id> <name> (and the browser must be running)"]); exit(1)
    }
    guard let (el, role, name) = webBestMatch(webCollect(areas, roles: webFieldRoles), args[3]) else {
        outputJSON(["error": "No text field named '\(args[3])' on the page"]); exit(1)
    }
    AXUIElementPerformAction(el, kAXPressAction as CFString)
    let r = AXUIElementSetAttributeValue(el, kAXFocusedAttribute as CFString, kCFBooleanTrue)
    if r == .success { outputJSON(["status": "ok", "focused": name, "type": shortRole(role)]) }
    else { outputJSON(["error": "Couldn't focus '\(name)' (error \(r.rawValue))"]); exit(1) }

case "web_links":
    guard args.count >= 4, let areas = webAreas(args[2]) else {
        outputJSON(["error": "Usage: web_links <bundle_id> <url_part> (and the browser must be running)"]); exit(1)
    }
    let part = args[3]
    var seen = Set<String>()
    var links: [[String: String]] = []
    for (el, _, name) in webCollect(areas, roles: ["AXLink"]) {
        guard let url = (axAttr(el, "AXURL") as? URL)?.absoluteString ?? (axAttr(el, "AXURL") as? String),
              url.contains(part), !seen.contains(url) else { continue }
        seen.insert(url)
        links.append(["name": name, "url": url])
        if links.count >= 40 { break }
    }
    outputJSON(["links": links])

case "web_text":
    guard args.count >= 3, let areas = webAreas(args[2]) else {
        outputJSON(["error": "Browser is not running"]); exit(1)
    }
    var parts: [String] = []
    var total = 0
    func walk(_ e: AXUIElement, _ d: Int) {
        if d > 80 || total > 6000 { return }
        if axRole(e) == "AXStaticText", let v = axAttr(e, kAXValueAttribute) as? String {
            let t = v.trimmingCharacters(in: .whitespacesAndNewlines)
            if !t.isEmpty && parts.last != t { parts.append(t); total += t.count }
        }
        for c in axChildren(e) { walk(c, d + 1) }
    }
    if let main = areas.first { walk(main, 0) }
    let title = areas.first.flatMap { axAttr($0, kAXTitleAttribute) as? String ?? axAttr($0, kAXDescriptionAttribute) as? String } ?? ""
    outputJSON(["page": title, "text": parts.joined(separator: "\n")])

case "music_play_album":
    // Press the Play button of the album page open in Music (waits for the page to load)
    guard AXIsProcessTrusted() else {
        outputJSON(["error": "Accessibility permission is needed to control Music"])
        exit(1)
    }
    let timeout = args.count > 2 ? (Double(args[2]) ?? 12) : 12
    // Optional: only press once this track title is on the page, so a stale album isn't played
    let expected = args.count > 3 ? normalized(args[3]) : ""
    func showsExpected(_ details: AXUIElement) -> Bool {
        if expected.isEmpty { return true }
        return axFind(details, { e in
            axRole(e) == "AXStaticText" && axLabels(e).contains { label in
                let t = normalized(label)
                return !t.isEmpty && (t == expected || t.hasPrefix(expected + " ") || expected.hasPrefix(t + " "))
            }
        }) != nil
    }
    guard let music = NSRunningApplication.runningApplications(withBundleIdentifier: "com.apple.Music").first else {
        outputJSON(["error": "Music is not running"])
        exit(1)
    }
    let root = AXUIElementCreateApplication(music.processIdentifier)
    let deadline = Date().addingTimeInterval(timeout)
    var playButton: AXUIElement?
    while playButton == nil && Date() < deadline {
        if let details = axFind(root, { axRole($0) == "AXScrollArea" && axLabels($0).contains("album details") }),
           showsExpected(details) {
            // The album's own Play button, not the per-row ones inside the track list
            playButton = axChildren(details).first { axRole($0) == "AXButton" && axLabels($0).contains { $0.lowercased() == "play" } }
        }
        if playButton == nil { usleep(300000) }
    }
    guard let button = playButton else {
        outputJSON(["error": expected.isEmpty ? "The album page did not load in Music" : "The song's album page did not load in Music"])
        exit(1)
    }
    if AXUIElementPerformAction(button, kAXPressAction as CFString) == .success {
        outputJSON(["status": "ok"])
    } else {
        outputJSON(["error": "Could not press the album Play button"])
        exit(1)
    }

default:
    outputJSON(["error": "Unknown command: \(cmd)"])
    exit(1)
}
