import Cocoa
import PDFKit
import ImageIO
import UniformTypeIdentifiers
@main struct Fixtures {
 static func main() throws {
  let directory=URL(fileURLWithPath:CommandLine.arguments[1]);try FileManager.default.createDirectory(at:directory,withIntermediateDirectories:true)
  let image=NSImage(size:NSSize(width:1400,height:800));image.lockFocus()
  NSColor.white.setFill();NSBezierPath(rect:NSRect(x:0,y:0,width:1400,height:800)).fill()
  let attrs:[NSAttributedString.Key:Any]=[.font:NSFont.systemFont(ofSize:64),.foregroundColor:NSColor.black]
  NSString(string:"SHIYUE ARCHIVE 2026").draw(at:NSPoint(x:100,y:600),withAttributes:attrs)
  NSString(string:"Offline document search").draw(at:NSPoint(x:100,y:440),withAttributes:attrs)
  NSString(string:"青山项目资料").draw(at:NSPoint(x:100,y:280),withAttributes:attrs)
  image.unlockFocus()
  let cg=image.cgImage(forProposedRect:nil,context:nil,hints:nil)!
  let png=CGImageDestinationCreateWithURL(directory.appendingPathComponent("ocr.png") as CFURL,UTType.png.identifier as CFString,1,nil)!
  CGImageDestinationAddImage(png,cg,nil);guard CGImageDestinationFinalize(png) else{fatalError("PNG")}
  let pdf=PDFDocument();pdf.insert(PDFPage(image:image)!,at:0);pdf.insert(PDFPage(image:image)!,at:1)
  guard pdf.write(to:directory.appendingPathComponent("scan.pdf")) else{fatalError("PDF")}
 }
}
