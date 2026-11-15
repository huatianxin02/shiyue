package local.shiyue.vault;
import android.content.Context;
import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.media.MediaMetadataRetriever;
import android.os.Build;
import android.os.Handler;
import android.os.Looper;
import android.util.LruCache;
import android.widget.ImageView;
import java.io.File;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
final class Thumbs {
 private static final ExecutorService pool=Executors.newFixedThreadPool(2);
 private static final Handler main=new Handler(Looper.getMainLooper());
 private static final LruCache<String,Bitmap> cache=new LruCache<String,Bitmap>(8*1024*1024){protected int sizeOf(String k,Bitmap b){return b.getByteCount();}};
 static Bitmap orient(File file,Bitmap source){
  if(source==null)return null;
  try{
   int value=new android.media.ExifInterface(file.getAbsolutePath()).getAttributeInt(android.media.ExifInterface.TAG_ORIENTATION,1);
   android.graphics.Matrix matrix=new android.graphics.Matrix();
   switch(value){case 2:matrix.setScale(-1,1);break;case 3:matrix.setRotate(180);break;case 4:matrix.setScale(1,-1);break;case 5:matrix.setRotate(90);matrix.postScale(-1,1);break;case 6:matrix.setRotate(90);break;case 7:matrix.setRotate(-90);matrix.postScale(-1,1);break;case 8:matrix.setRotate(270);break;default:return source;}
   Bitmap turned=Bitmap.createBitmap(source,0,0,source.getWidth(),source.getHeight(),matrix,true);if(turned!=source)source.recycle();return turned;
  }catch(Exception ignored){return source;}
 }
 static void load(Context context,final Pack.Record record,final ImageView target){
  target.setImageDrawable(null);target.setTag(record.sha);
  if(!"image".equals(record.kind)&&!"video".equals(record.kind))return;
  Bitmap hit=cache.get(record.sha);if(hit!=null){target.setImageBitmap(hit);return;}
  final File file=Library.objectFile(context.getApplicationContext(),record.sha);
  pool.execute(new Runnable(){public void run(){
   Bitmap b=null;
   try{
    if("video".equals(record.kind)){
     MediaMetadataRetriever retriever=new MediaMetadataRetriever();
     try{retriever.setDataSource(file.getAbsolutePath());b=Build.VERSION.SDK_INT>=27?retriever.getScaledFrameAtTime(0,MediaMetadataRetriever.OPTION_CLOSEST_SYNC,128,128):retriever.getFrameAtTime(0);}
     finally{retriever.release();}
    }else{
     BitmapFactory.Options opt=new BitmapFactory.Options();opt.inJustDecodeBounds=true;BitmapFactory.decodeFile(file.getAbsolutePath(),opt);
     opt.inSampleSize=1;while(Math.max(opt.outWidth,opt.outHeight)/opt.inSampleSize>256)opt.inSampleSize*=2;
     opt.inJustDecodeBounds=false;b=BitmapFactory.decodeFile(file.getAbsolutePath(),opt);
    }
    if(b!=null){b=orient(file,b);final Bitmap ready=android.media.ThumbnailUtils.extractThumbnail(b,96,96);if(ready!=b)b.recycle();cache.put(record.sha,ready);main.post(new Runnable(){public void run(){if(record.sha.equals(target.getTag()))target.setImageBitmap(ready);}});}
   }catch(Exception|OutOfMemoryError ignored){}
  }});
 }
}
