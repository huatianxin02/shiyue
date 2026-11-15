import Cocoa
import WebKit
import UniformTypeIdentifiers

final class LibraryWebView: WKWebView {
    var libraryOrigin: URL?
    private func fileURLs(_ sender: NSDraggingInfo) -> [URL] {
        guard url?.host == "127.0.0.1", url?.port == libraryOrigin?.port else { return [] }
        return (sender.draggingPasteboard.readObjects(forClasses:[NSURL.self],options:[.urlReadingFileURLsOnly:true]) as? [URL]) ?? []
    }
    private func indicate(_ active: Bool) {
        evaluateJavaScript("window.dispatchEvent(new CustomEvent('native-drag-state',{detail:{active:\(active)}}))",completionHandler:nil)
    }
    override func draggingEntered(_ sender:NSDraggingInfo)->NSDragOperation {
        if !fileURLs(sender).isEmpty { indicate(true);return .copy }
        return super.draggingEntered(sender)
    }
    override func draggingUpdated(_ sender:NSDraggingInfo)->NSDragOperation {
        return fileURLs(sender).isEmpty ? super.draggingUpdated(sender) : .copy
    }
    override func draggingExited(_ sender:NSDraggingInfo?){ indicate(false);super.draggingExited(sender) }
    override func prepareForDragOperation(_ sender:NSDraggingInfo)->Bool {
        let count=fileURLs(sender).count
        return count==0 ? super.prepareForDragOperation(sender) : count<=1000
    }
    override func performDragOperation(_ sender:NSDraggingInfo)->Bool {
        let urls=fileURLs(sender);indicate(false)
        if urls.isEmpty { return super.performDragOperation(sender) }
        guard urls.count<=1000 else{return false}
        guard let data=try? JSONSerialization.data(withJSONObject:["paths":urls.map{$0.path}]),let json=String(data:data,encoding:.utf8) else{return false}
        evaluateJavaScript("window.dispatchEvent(new CustomEvent('native-drop',{detail:\(json)}))",completionHandler:nil);return true
    }
}

final class AppDelegate: NSObject, NSApplicationDelegate, WKScriptMessageHandler, WKNavigationDelegate, WKUIDelegate, WKDownloadDelegate {
    var window: NSWindow!
    var webView: LibraryWebView!
    var process: Process?
    var origin: URL?
    var readyURL: URL!
    var timer: Timer?
    var attempts = 0
    var logHandle: FileHandle?

    func applicationDidFinishLaunching(_ notification: Notification) {
        let menu = NSMenu()
        let mainItem = NSMenuItem(); menu.addItem(mainItem)
        let appMenu = NSMenu(); mainItem.submenu = appMenu
        appMenu.addItem(withTitle: "关于拾页", action: #selector(about), keyEquivalent: "")
        appMenu.addItem(withTitle: "设置…", action: #selector(openSettings), keyEquivalent: ",")
        appMenu.addItem(NSMenuItem.separator())
        appMenu.addItem(withTitle: "退出拾页", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        let editItem = NSMenuItem(); menu.addItem(editItem)
        let edit = NSMenu(title: "编辑"); editItem.submenu = edit
        edit.addItem(withTitle: "撤销", action: Selector(("undo:")), keyEquivalent: "z")
        edit.addItem(withTitle: "剪切", action: #selector(NSText.cut(_:)), keyEquivalent: "x")
        edit.addItem(withTitle: "复制", action: #selector(NSText.copy(_:)), keyEquivalent: "c")
        edit.addItem(withTitle: "粘贴", action: #selector(NSText.paste(_:)), keyEquivalent: "v")
        edit.addItem(withTitle: "全选", action: #selector(NSText.selectAll(_:)), keyEquivalent: "a")
        NSApp.mainMenu = menu
        window = NSWindow(contentRect: NSRect(x:0,y:0,width:1280,height:860), styleMask:[.titled,.closable,.miniaturizable,.resizable], backing:.buffered, defer:false)
        window.title = "拾页 · 本地资料库"; window.minSize = NSSize(width:850,height:620); window.center()
        window.backgroundColor = NSColor(calibratedRed:0.969,green:0.973,blue:0.980,alpha:1)
        let config = WKWebViewConfiguration()
        config.userContentController.add(self,name:"native")
        config.websiteDataStore = .nonPersistent()
        // The server uses a new port on each launch. Persist preferences in the
        // native app rather than origin-scoped localStorage on that random port.
        var preferences: [String: Bool] = [:]
        for key in ["sources", "collections"] {
            if UserDefaults.standard.object(forKey: "sidebar.fold." + key) != nil {
                preferences[key] = UserDefaults.standard.bool(forKey: "sidebar.fold." + key)
            }
        }
        if let data = try? JSONSerialization.data(withJSONObject: preferences), let json = String(data: data, encoding: .utf8) {
            config.userContentController.addUserScript(WKUserScript(source: "window.shiyuePreferences=\(json);", injectionTime: .atDocumentStart, forMainFrameOnly: true))
        }
        webView = LibraryWebView(frame:window.contentView!.bounds,configuration:config)
        webView.registerForDraggedTypes([.fileURL])
        webView.autoresizingMask = [.width,.height]; webView.navigationDelegate=self; webView.uiDelegate=self
        window.contentView?.addSubview(webView)
        window.makeKeyAndOrderFront(nil); NSApp.activate(ignoringOtherApps:true)
        webView.loadHTMLString("<html><body style='background:#f7f8fa;font:16px -apple-system;text-align:center;padding-top:26%;color:#26786a'>拾页<br><small style='color:#91a096'>正在打开本地资料库…</small></body></html>",baseURL:nil)
        launchBackend()
    }
    /// Where the library lives.
    ///
    /// A copy of the app that sits next to a `data` folder stays portable, which is
    /// how the development build works. An installed copy in /Applications cannot
    /// write beside itself, and its data belongs in Application Support anyway.
    func resolveDataDirectory() -> URL {
        let environment=ProcessInfo.processInfo.environment
        if let custom=environment["SHIYE_DATA"],!custom.isEmpty { return URL(fileURLWithPath:(custom as NSString).expandingTildeInPath) }
        if let selected=UserDefaults.standard.string(forKey:"dataDirectory"),
           FileManager.default.fileExists(atPath:URL(fileURLWithPath:selected).appendingPathComponent("library.sqlite3").path) {
            return URL(fileURLWithPath:selected)
        }
        let portable=Bundle.main.bundleURL.deletingLastPathComponent().appendingPathComponent("data")
        if FileManager.default.fileExists(atPath:portable.path) { return portable }
        return FileManager.default.urls(for:.applicationSupportDirectory,in:.userDomainMask)[0].appendingPathComponent("拾页资料库")
    }
    var pendingCitation: String?
    var pageReady = false
    func application(_ application: NSApplication, open urls: [URL]) {
        guard let url=urls.first, url.scheme=="shiyue",url.host=="files",url.absoluteString.count<2048 else { return }
        pendingCitation=url.absoluteString
        if pageReady { deliverCitation() }
    }
    func deliverCitation() {
        guard let value=pendingCitation, let data=try? JSONSerialization.data(withJSONObject:["url":value]),let json=String(data:data,encoding:.utf8) else { return }
        pendingCitation=nil
        webView.evaluateJavaScript("window.dispatchEvent(new CustomEvent(\"native-citation\",{detail:\(json)}))",completionHandler:nil)
    }
    func webView(_ webView:WKWebView,didFinish navigation:WKNavigation!) {
        if webView.url?.host=="127.0.0.1" {pageReady=true;deliverCitation()}
    }
    func launchBackend() {
        let resources=Bundle.main.resourceURL!
        let runtime=resources.appendingPathComponent("runtime")
        let data=resolveDataDirectory()
        do { try FileManager.default.createDirectory(at:data,withIntermediateDirectories:true) } catch { fail("无法建立数据目录：\(error.localizedDescription)"); return }
        readyURL=data.appendingPathComponent(".ready-\(UUID().uuidString).json")
        let candidates=[resources.appendingPathComponent("python/bin/python3").path,"/opt/homebrew/bin/python3","/usr/local/bin/python3","/usr/bin/python3"]
        var selectedPython: String?
        var diagnostics: [String] = []
        for candidate in candidates where FileManager.default.isExecutableFile(atPath:candidate) {
            let check=Process(); check.executableURL=URL(fileURLWithPath:candidate)
            check.arguments=["-c","import sys,sqlite3,pypdf; assert sys.version_info>=(3,10); c=sqlite3.connect(':memory:'); c.execute('CREATE VIRTUAL TABLE probe USING fts5(text)'); c.close()"]
            var env=ProcessInfo.processInfo.environment; env["PYTHONPATH"]=runtime.appendingPathComponent("vendor").path
            env["PYTHONDONTWRITEBYTECODE"]="1"; check.environment=env
            let errorPipe=Pipe(); check.standardError=errorPipe; check.standardOutput=Pipe()
            do { try check.run(); check.waitUntilExit()
                if check.terminationStatus==0 { selectedPython=candidate; break }
                diagnostics.append(candidate+": "+(String(data:errorPipe.fileHandleForReading.readDataToEndOfFile(),encoding:.utf8) ?? "解释器自检失败").suffix(600))
            } catch { diagnostics.append(candidate+": "+error.localizedDescription) }
        }
        guard let python=selectedPython else { fail("运行环境检查未通过。需要 Python 3.10+、SQLite FTS5 和 PDF 组件。\n"+diagnostics.joined(separator:"\n")); return }
        let proc=Process();proc.executableURL=URL(fileURLWithPath:python)
        proc.arguments=[runtime.appendingPathComponent("server.py").path,"--data",data.path,"--port","0","--ready",readyURL.path]
        proc.currentDirectoryURL=runtime
        var environment=ProcessInfo.processInfo.environment
        environment["PYTHONDONTWRITEBYTECODE"]="1"
        environment["PYTHONPATH"]=runtime.appendingPathComponent("vendor").path
        proc.environment=environment
        let log=data.appendingPathComponent("app.log")
        FileManager.default.createFile(atPath:log.path,contents:nil)
        logHandle=try? FileHandle(forWritingTo:log);proc.standardOutput=logHandle;proc.standardError=logHandle
        process=proc
        do { try proc.run() } catch { fail("本地服务启动失败：\(error.localizedDescription)");return }
        timer=Timer.scheduledTimer(withTimeInterval:0.15,repeats:true){[weak self] timer in
            guard let self=self else{return};self.attempts+=1
            if let data=try? Data(contentsOf:self.readyURL),let json=try? JSONSerialization.jsonObject(with:data) as? [String:Any],let value=json["url"] as? String,let url=URL(string:value) {
                timer.invalidate();self.origin=url;self.webView.libraryOrigin=url;self.webView.load(URLRequest(url:url));try? FileManager.default.removeItem(at:self.readyURL)
            } else if !proc.isRunning {
                timer.invalidate();self.fail("本地服务已退出。请查看资料库目录中的 app.log；若已打开其他实例，请先关闭它。")
            } else if self.attempts>800 {
                timer.invalidate();self.fail("准备资料库超过两分钟。请查看资料库目录中的 app.log 后重试。")
            } else if self.attempts==200 {
                // A newly copied, ad-hoc signed runtime can be slow on its first
                // launch. Keep a live process eligible to become ready instead
                // of discarding the ready file just after the old 30 s cutoff.
                self.webView.loadHTMLString("<html><body style='background:#f7f8fa;font:16px -apple-system;text-align:center;padding-top:26%;color:#26786a'>拾页<br><small style='color:#74817c'>正在准备资料库，首次打开可能需要更久…</small></body></html>",baseURL:nil)
            }
        }
    }
    func userContentController(_ controller:WKUserContentController,didReceive message:WKScriptMessage) {
        guard message.frameInfo.isMainFrame, let host=message.frameInfo.request.url?.host, host=="127.0.0.1",message.frameInfo.request.url?.port==origin?.port,let body=message.body as? [String:Any] else{return}
        if body["action"] as? String == "switch-library", let path=body["path"] as? String {
            let directory=URL(fileURLWithPath:path)
            guard FileManager.default.fileExists(atPath:directory.appendingPathComponent("library.sqlite3").path) else { return }
            let previous=UserDefaults.standard.string(forKey:"dataDirectory")
            UserDefaults.standard.set(path,forKey:"dataDirectory")
            let configuration=NSWorkspace.OpenConfiguration(); configuration.createsNewApplicationInstance=true
            NSWorkspace.shared.openApplication(at:Bundle.main.bundleURL,configuration:configuration){ _,error in
                DispatchQueue.main.async {
                    if error == nil { NSApp.terminate(nil) }
                    else { UserDefaults.standard.set(previous,forKey:"dataDirectory") }
                }
            }
            return
        }
        if body["action"] as? String == "preference", let key = body["key"] as? String,
           ["sources", "collections"].contains(key), let folded = body["folded"] as? Bool {
            UserDefaults.standard.set(folded, forKey: "sidebar.fold." + key)
            return
        }
        guard body["action"] as? String=="pick",let kind=body["kind"] as? String,["files","folder","pack","backup","repository","discovery","relocate-file","relocate-folder"].contains(kind) else{return}
        let panel=NSOpenPanel();panel.canChooseDirectories=["folder","relocate-folder","repository","discovery"].contains(kind);panel.canChooseFiles = !["folder","relocate-folder","repository","discovery"].contains(kind);panel.allowsMultipleSelection=kind=="files";panel.prompt="选择";if kind=="backup" { panel.allowedContentTypes=[UTType(filenameExtension:"shiyuebackup") ?? .data] };panel.message=kind=="backup" ? "选择拾页完整库备份（.shiyuebackup）" : kind=="pack" ? "选择拾页完整资料包（.vaultpack）" : "选择要加入资料库的文件或文件夹"
        panel.beginSheetModal(for:window){[weak self] response in
            guard response == .OK else{return}
            let payload:[String:Any]=["kind":kind,"paths":panel.urls.map{$0.path}]
            if let data=try? JSONSerialization.data(withJSONObject:payload),let json=String(data:data,encoding:.utf8){self?.webView.evaluateJavaScript("window.dispatchEvent(new CustomEvent('native-selection',{detail:\(json)}))",completionHandler:nil)}
        }
    }
    func webView(_ webView:WKWebView,decidePolicyFor navigationAction:WKNavigationAction,decisionHandler:@escaping(WKNavigationActionPolicy)->Void){
        if navigationAction.shouldPerformDownload { decisionHandler(.download);return }
        guard let url=navigationAction.request.url else{decisionHandler(.cancel);return}
        if url.scheme=="about" || url.scheme=="blob" || (url.host=="127.0.0.1" && url.port==origin?.port){decisionHandler(.allow)}else{decisionHandler(.cancel)}
    }
    func webView(_ webView: WKWebView, runJavaScriptConfirmPanelWithMessage message: String, initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping (Bool) -> Void) {
        guard frame.isMainFrame, frame.request.url?.host == "127.0.0.1", frame.request.url?.port == origin?.port else { completionHandler(false); return }
        let alert = NSAlert(); alert.messageText = "核对这次操作"; alert.informativeText = message
        alert.addButton(withTitle: "确认"); alert.addButton(withTitle: "取消")
        alert.beginSheetModal(for: window) { result in completionHandler(result == .alertFirstButtonReturn) }
    }
    func webView(_ webView: WKWebView, navigationAction: WKNavigationAction, didBecome download: WKDownload) { download.delegate = self }
    func webView(_ webView: WKWebView, navigationResponse: WKNavigationResponse, didBecome download: WKDownload) { download.delegate = self }
    func download(_ download: WKDownload, decideDestinationUsing response: URLResponse, suggestedFilename: String, completionHandler: @escaping (URL?) -> Void) {
        let panel = NSSavePanel(); panel.nameFieldStringValue = suggestedFilename; panel.canCreateDirectories = true
        panel.beginSheetModal(for: window) { result in completionHandler(result == .OK ? panel.url : nil) }
    }
    func applicationShouldTerminateAfterLastWindowClosed(_ sender:NSApplication)->Bool{return true}
    func applicationWillTerminate(_ notification:Notification){timer?.invalidate();if process?.isRunning==true {process?.terminate();process?.waitUntilExit()};if let url=readyURL{try? FileManager.default.removeItem(at:url)};try? logHandle?.close()}
    @objc func openSettings(){
        guard pageReady else { return }
        webView.evaluateJavaScript("window.dispatchEvent(new CustomEvent('native-open-settings'))",completionHandler:nil)
    }
    @objc func about(){
        // Read the version from the bundle so this dialog can never drift from the
        // release again, and show the real data location: an installed copy keeps
        // its library in Application Support, not next to the app.
        let version=Bundle.main.object(forInfoDictionaryKey:"CFBundleShortVersionString") as? String ?? "1.0.0"
        let home=NSHomeDirectory()
        let data=resolveDataDirectory().path.replacingOccurrences(of:home,with:"~")
        let alert=NSAlert();alert.messageText="拾页 · 本地资料库"
        alert.informativeText="版本 \(version)\n本地存储、全文搜索、分类索引、可验证的资料迁移。\n\n资料库位置：\(data)\n资料索引默认保存在本机。外部 AI 客户端的传输由其设置决定。\nMIT 开源；第三方许可随发行包保留。"
        alert.runModal()
    }
    func fail(_ message:String){let alert=NSAlert();alert.messageText="拾页启动遇到问题";alert.informativeText=message;alert.alertStyle = .warning;alert.runModal();NSApp.terminate(nil)}
}
let app=NSApplication.shared
let delegate=AppDelegate()
app.delegate=delegate
app.setActivationPolicy(.regular)
app.run()
