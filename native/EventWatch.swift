import Foundation
import CoreServices

// The Python scheduler does all indexing. Events only request a debounced rescan.
guard let line = readLine(), let data = line.data(using: .utf8),
      let roots = try? JSONDecoder().decode([String].self, from: data), !roots.isEmpty else { exit(2) }
let callback: FSEventStreamCallback = { _, _, count, paths, flags, _ in
    let paths = unsafeBitCast(paths, to: NSArray.self) as! [String]
    let dropped = FSEventStreamEventFlags(kFSEventStreamEventFlagMustScanSubDirs | kFSEventStreamEventFlagUserDropped | kFSEventStreamEventFlagKernelDropped | kFSEventStreamEventFlagRootChanged)
    var rescan = false
    for index in 0..<count { if flags[index] & dropped != 0 { rescan = true } }
    if let bytes = try? JSONSerialization.data(withJSONObject: ["paths": paths, "rescan": rescan]),
       let text = String(data: bytes, encoding: .utf8) {
        print(text); fflush(stdout)
    }
}
let flags = FSEventStreamCreateFlags(kFSEventStreamCreateFlagUseCFTypes | kFSEventStreamCreateFlagFileEvents | kFSEventStreamCreateFlagWatchRoot | kFSEventStreamCreateFlagNoDefer)
guard let stream = FSEventStreamCreate(nil, callback, nil, roots as CFArray, FSEventStreamEventId(kFSEventStreamEventIdSinceNow), 1.0, flags) else { exit(3) }
FSEventStreamSetDispatchQueue(stream, DispatchQueue.global())
guard FSEventStreamStart(stream) else { exit(4) }
print("{\"ready\":true}"); fflush(stdout)
dispatchMain()
