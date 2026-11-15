import Foundation
import CoreGraphics
import CoreVideo
import ImageIO
import AVFoundation
import UniformTypeIdentifiers

func image(_ destination:URL, type:UTType) throws {
    let color=CGColorSpaceCreateDeviceRGB()
    let ctx=CGContext(data:nil,width:640,height:480,bitsPerComponent:8,bytesPerRow:0,space:color,bitmapInfo:CGImageAlphaInfo.noneSkipLast.rawValue)!
    ctx.setFillColor(CGColor(red:0.2,green:0.6,blue:0.4,alpha:1));ctx.fill(CGRect(x:0,y:0,width:640,height:480))
    let dst=CGImageDestinationCreateWithURL(destination as CFURL,type.identifier as CFString,1,nil)!
    let props:[CFString:Any]=[kCGImagePropertyOrientation:6,kCGImagePropertyExifDictionary:[kCGImagePropertyExifDateTimeOriginal:"2024:06:18 15:30:00","OffsetTimeOriginal":"+08:00"],kCGImagePropertyTIFFDictionary:[kCGImagePropertyTIFFMake:"Test Camera Maker",kCGImagePropertyTIFFModel:"QA Phone"],kCGImagePropertyGPSDictionary:[kCGImagePropertyGPSLatitude:31.23,kCGImagePropertyGPSLatitudeRef:"N",kCGImagePropertyGPSLongitude:121.47,kCGImagePropertyGPSLongitudeRef:"E"]]
    CGImageDestinationAddImage(dst,ctx.makeImage()!,props as CFDictionary)
    if !CGImageDestinationFinalize(dst){throw NSError(domain:"fixtures",code:1)}
}
func meta(_ id:AVMetadataIdentifier,_ value:String)->AVMetadataItem {
    let item=AVMutableMetadataItem();item.identifier=id;item.value=value as NSString;item.dataType=kCMMetadataBaseDataType_UTF8 as String;return item
}
func video(_ url:URL, fileType:AVFileType) async throws {
    let writer=try AVAssetWriter(outputURL:url,fileType:fileType)
    writer.metadata=[meta(.quickTimeMetadataCreationDate,"2023-08-09T10:11:12+08:00"),meta(.quickTimeMetadataMake,"Test Camera Maker"),meta(.quickTimeMetadataModel,"QA Video Phone")]
    let input=AVAssetWriterInput(mediaType:.video,outputSettings:[AVVideoCodecKey:AVVideoCodecType.h264,AVVideoWidthKey:320,AVVideoHeightKey:240]);input.expectsMediaDataInRealTime=false
    let adapter=AVAssetWriterInputPixelBufferAdaptor(assetWriterInput:input,sourcePixelBufferAttributes:[kCVPixelBufferPixelFormatTypeKey as String:kCVPixelFormatType_32ARGB,kCVPixelBufferWidthKey as String:320,kCVPixelBufferHeightKey as String:240])
    writer.add(input);guard writer.startWriting() else{throw writer.error!};writer.startSession(atSourceTime:.zero)
    for frame in 0..<20 {
        while !input.isReadyForMoreMediaData {try await Task.sleep(nanoseconds:1_000_000)}
        var buffer:CVPixelBuffer?;CVPixelBufferPoolCreatePixelBuffer(nil,adapter.pixelBufferPool!,&buffer)
        CVPixelBufferLockBaseAddress(buffer!,[])
        let p=CVPixelBufferGetBaseAddress(buffer!)!.assumingMemoryBound(to:UInt8.self)
        let stride=CVPixelBufferGetBytesPerRow(buffer!)
        for y in 0..<240 {for x in 0..<320{let i=y*stride+x*4;p[i]=255;p[i+1]=UInt8(frame*10);p[i+2]=120;p[i+3]=80}}
        CVPixelBufferUnlockBaseAddress(buffer!,[])
        guard adapter.append(buffer!,withPresentationTime:CMTime(value:Int64(frame),timescale:10)) else{throw writer.error!}
    }
    input.markAsFinished();await writer.finishWriting();if writer.status != .completed{throw writer.error ?? NSError(domain:"fixtures",code:2)}
}
@main struct Fixtures {
 static func main() async throws {
    let dir=URL(fileURLWithPath:CommandLine.arguments[1]);try FileManager.default.createDirectory(at:dir,withIntermediateDirectories:true)
    try image(dir.appendingPathComponent("camera.jpg"),type:.jpeg)
    try image(dir.appendingPathComponent("camera.heic"),type:.heic)
    try image(dir.appendingPathComponent("camera.tiff"),type:.tiff)
    try await video(dir.appendingPathComponent("real.mp4"),fileType:.mp4)
    try await video(dir.appendingPathComponent("real.mov"),fileType:.mov)
 }
}
