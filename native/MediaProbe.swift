import Foundation
import ImageIO
import AVFoundation
import UniformTypeIdentifiers

func writeJPEG(_ image: CGImage, to url: URL) throws {
    guard let destination=CGImageDestinationCreateWithURL(url as CFURL,UTType.jpeg.identifier as CFString,1,nil) else {throw NSError(domain:"MediaProbe",code:1,userInfo:[NSLocalizedDescriptionKey:"无法创建缩略图"])}
    CGImageDestinationAddImage(destination,image,[kCGImageDestinationLossyCompressionQuality:0.78] as CFDictionary)
    guard CGImageDestinationFinalize(destination) else {throw NSError(domain:"MediaProbe",code:2,userInfo:[NSLocalizedDescriptionKey:"缩略图写入失败"])}
}
func string(_ value: Any?) -> String? { guard let v=value as? String, !v.trimmingCharacters(in:.whitespacesAndNewlines).isEmpty else{return nil};return String(v.prefix(300)) }
func imageInfo(_ url: URL, _ thumb: URL) throws -> [String:Any] {
    guard let source=CGImageSourceCreateWithURL(url as CFURL,[kCGImageSourceShouldCache:false] as CFDictionary),let props=CGImageSourceCopyPropertiesAtIndex(source,0,nil) as? [String:Any] else {throw NSError(domain:"MediaProbe",code:3,userInfo:[NSLocalizedDescriptionKey:"系统无法读取此图片"])}
    var result:[String:Any]=[:]
    var width=(props[kCGImagePropertyPixelWidth as String] as? NSNumber)?.intValue ?? 0
    var height=(props[kCGImagePropertyPixelHeight as String] as? NSNumber)?.intValue ?? 0
    let orientation=(props[kCGImagePropertyOrientation as String] as? NSNumber)?.intValue ?? 1
    if [5,6,7,8].contains(orientation){swap(&width,&height)}
    if width>0 {result["width"]=width};if height>0 {result["height"]=height}
    let exif=props[kCGImagePropertyExifDictionary as String] as? [String:Any] ?? [:]
    let tiff=props[kCGImagePropertyTIFFDictionary as String] as? [String:Any] ?? [:]
    let gps=props[kCGImagePropertyGPSDictionary as String] as? [String:Any] ?? [:]
    if let value=string(exif[kCGImagePropertyExifDateTimeOriginal as String]) {
        result["captured_at"]=value+(string(exif["OffsetTimeOriginal"]) ?? "");result["captured_source"]="EXIF 拍摄时间"
    } else if let value=string(exif[kCGImagePropertyExifDateTimeDigitized as String]) {
        result["captured_at"]=value;result["captured_source"]="EXIF 数字化时间"
    } else if let value=string(tiff[kCGImagePropertyTIFFDateTime as String]) {
        result["captured_at"]=value;result["captured_source"]="TIFF 图像时间"
    }
    result["make"]=string(tiff[kCGImagePropertyTIFFMake as String]);result["camera"]=string(tiff[kCGImagePropertyTIFFModel as String])
    if let lat=gps[kCGImagePropertyGPSLatitude as String] as? NSNumber,let lon=gps[kCGImagePropertyGPSLongitude as String] as? NSNumber {
        result["gps_lat"]=lat.doubleValue * ((gps[kCGImagePropertyGPSLatitudeRef as String] as? String)=="S" ? -1:1)
        result["gps_lon"]=lon.doubleValue * ((gps[kCGImagePropertyGPSLongitudeRef as String] as? String)=="W" ? -1:1)
    }
    let options:[CFString:Any]=[kCGImageSourceCreateThumbnailFromImageAlways:true,kCGImageSourceThumbnailMaxPixelSize:512,kCGImageSourceCreateThumbnailWithTransform:true,kCGImageSourceShouldCacheImmediately:true]
    if let image=CGImageSourceCreateThumbnailAtIndex(source,0,options as CFDictionary) {try writeJPEG(image,to:thumb)}
    else {result["thumbnail_error"]="系统无法生成此图片的预览"}
    return result
}
func videoInfo(_ url: URL, _ thumb: URL) async throws -> [String:Any] {
    let asset=AVURLAsset(url:url);var result:[String:Any]=[:]
    let duration=try await asset.load(.duration);let seconds=CMTimeGetSeconds(duration)
    if seconds.isFinite && seconds>=0 {result["duration"]=seconds}
    let tracks=try await asset.loadTracks(withMediaType:.video)
    if let track=tracks.first {
        let size=try await track.load(.naturalSize);let transform=try await track.load(.preferredTransform)
        let display=size.applying(transform);result["width"]=Int(abs(display.width).rounded());result["height"]=Int(abs(display.height).rounded())
        let descriptions=try await track.load(.formatDescriptions)
        if let description=descriptions.first {let code=CMFormatDescriptionGetMediaSubType(description);result["codec"]=String(bytes:[UInt8((code>>24)&255),UInt8((code>>16)&255),UInt8((code>>8)&255),UInt8(code&255)],encoding:.ascii) ?? ""}
    }
    var metadata=try await asset.load(.commonMetadata)
    for format in try await asset.load(.availableMetadataFormats) {metadata += (try? await asset.loadMetadata(for:format)) ?? []}
    for item in metadata.prefix(256) {
        let key=(item.identifier?.rawValue ?? String(describing:item.key ?? "" as NSString)).lowercased()
        guard let value=try? await item.load(.stringValue),!value.isEmpty else{continue}
        if key.contains("creationdate") {result["captured_at"]=String(value.prefix(100));result["captured_source"]="QuickTime 拍摄时间"}
        if key.hasSuffix("/model") || key.hasSuffix(".model") {result["camera"]=String(value.prefix(300))}
        if key.hasSuffix("/make") || key.hasSuffix(".make") {result["make"]=String(value.prefix(300))}
    }
    if result["captured_at"]==nil,let creation=try? await asset.load(.creationDate),let date=try? await creation.load(.dateValue) {
        result["captured_at"]=ISO8601DateFormatter().string(from:date);result["captured_source"]="容器创建时间"
    }
    if !tracks.isEmpty {
        let generator=AVAssetImageGenerator(asset:asset);generator.appliesPreferredTrackTransform=true;generator.maximumSize=CGSize(width:512,height:512)
        let at=CMTime(seconds:seconds.isFinite ? min(max(seconds/10,0),1):0,preferredTimescale:600)
        do {let (image,_)=try await generator.image(at:at);try writeJPEG(image,to:thumb)} catch {result["thumbnail_error"]="无法解码视频封面：\(error.localizedDescription)"}
    }
    return result
}
@main struct MediaProbe {
    static func main() async {
        let args=CommandLine.arguments
        guard args.count==4 else {print("{\"error\":\"参数无效\"}");return}
        var result:[String:Any]
        do {
            let url=URL(fileURLWithPath:args[2]);let thumb=URL(fileURLWithPath:args[3])
            if args[1]=="video" {result=try await videoInfo(url,thumb)}else{result=try imageInfo(url,thumb)}
        }catch {result=["error":error.localizedDescription]}
        if let data=try? JSONSerialization.data(withJSONObject:result,options:[.sortedKeys]),let text=String(data:data,encoding:.utf8){print(text)}else{print("{\"error\":\"元数据格式错误\"}")}
    }
}
