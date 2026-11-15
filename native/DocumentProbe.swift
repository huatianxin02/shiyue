import Cocoa
import PDFKit
import Vision
import ImageIO
import UniformTypeIdentifiers

@main struct DocumentProbe {
    static func main() {
        do {
            guard CommandLine.arguments.count > 1,
                  let raw = CommandLine.arguments[1].data(using: .utf8),
                  let request = try JSONSerialization.jsonObject(with: raw) as? [String: Any],
                  let path = request["path"] as? String else { throw failure("请求格式无效") }
            let url = URL(fileURLWithPath: path)
            let mode = request["mode"] as? String ?? "info"
            let handle=try FileHandle(forReadingFrom:url)
            let prefix=try handle.read(upToCount:5);try handle.close()
            let pdf = prefix == Data("%PDF-".utf8) ? PDFDocument(url: url) : nil
            let pageNumber = request["page"] as? Int ?? 1
            let count = pdf?.pageCount ?? 1
            guard pageNumber >= 1, pageNumber <= count else { throw failure("页码超出范围") }
            if mode == "info" { try send(["pages": count, "pdf": pdf != nil]); return }
            let cg: CGImage
            if let pdf = pdf, let page = pdf.page(at: pageNumber - 1) {
                let box = page.bounds(for: .cropBox)
                guard box.width > 0, box.height > 0 else { throw failure("页面尺寸无效") }
                let factor = min(2400 / max(box.width, box.height), 3)
                let image = page.thumbnail(of: NSSize(width: box.width * factor, height: box.height * factor), for: .cropBox)
                guard let rendered = image.cgImage(forProposedRect: nil, context: nil, hints: nil) else { throw failure("无法渲染 PDF") }
                cg = rendered
            } else {
                guard let source = CGImageSourceCreateWithURL(url as CFURL, nil),
                      let rendered = CGImageSourceCreateThumbnailAtIndex(source, 0, [
                        kCGImageSourceCreateThumbnailFromImageAlways: true,
                        kCGImageSourceCreateThumbnailWithTransform: true,
                        kCGImageSourceThumbnailMaxPixelSize: 2400
                      ] as CFDictionary) else { throw failure("无法读取图片") }
                cg = rendered
            }
            if mode == "page" {
                guard let output = request["output"] as? String,
                      let dest = CGImageDestinationCreateWithURL(URL(fileURLWithPath: output) as CFURL, UTType.jpeg.identifier as CFString, 1, nil) else { throw failure("无法保存预览") }
                CGImageDestinationAddImage(dest, cg, [kCGImageDestinationLossyCompressionQuality: 0.88] as CFDictionary)
                guard CGImageDestinationFinalize(dest) else { throw failure("预览写入失败") }
                try send(["page": pageNumber]); return
            }
            guard mode == "ocr" else { throw failure("未知文档操作") }
            let task = VNRecognizeTextRequest()
            task.recognitionLevel = .accurate
            task.usesLanguageCorrection = true
            let supported = try task.supportedRecognitionLanguages()
            task.recognitionLanguages = ["zh-Hans", "zh-Hant", "en-US"].filter { supported.contains($0) }
            task.automaticallyDetectsLanguage = true
            try VNImageRequestHandler(cgImage: cg).perform([task])
            let lines: [[String: Any]] = (task.results ?? []).compactMap { result in
                guard let candidate = result.topCandidates(1).first else { return nil }
                let b = result.boundingBox
                return ["text": candidate.string, "confidence": candidate.confidence,
                        "bounds": [b.origin.x, b.origin.y, b.width, b.height]]
            }
            try send(["page": pageNumber, "lines": lines])
        } catch {
            FileHandle.standardError.write(Data(error.localizedDescription.utf8)); exit(1)
        }
    }
    static func failure(_ text: String) -> NSError { NSError(domain: "Shiyue", code: 1, userInfo: [NSLocalizedDescriptionKey: text]) }
    static func send(_ value: [String: Any]) throws {
        let data = try JSONSerialization.data(withJSONObject: value, options: [.sortedKeys])
        FileHandle.standardOutput.write(data)
    }
}
