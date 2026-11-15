package local.shiyue.vault;
import android.app.Instrumentation;
import android.content.Context;
import android.content.ContextWrapper;
import android.os.Bundle;
import java.io.*;
import java.util.*;

/** Real Android SQLite tests, using a separate test app and disposable files only. */
public final class IntegrityInstrumentation extends Instrumentation {
 private int passed;
 private void check(boolean ok,String message){if(!ok)throw new AssertionError(message);passed++;}
 @Override public void onCreate(Bundle args){super.onCreate(args);start();}
 @Override public void onStart(){
  Bundle result=new Bundle();Library library=null;
  try{
   final Context target=getTargetContext();final File isolated=new File(target.getCacheDir(),"case-"+System.nanoTime());isolated.mkdirs();
   Context context=new ContextWrapper(target){@Override public File getFilesDir(){return isolated;}};
   library=new Library(context);
   File pack=new File(isolated,"fixture.vaultpack");
   int resource=target.getResources().getIdentifier("fixture","raw",target.getPackageName());
   try(InputStream in=target.getResources().openRawResource(resource);OutputStream out=new FileOutputStream(pack)){byte[] b=new byte[8192];int n;while((n=in.read(b))>0)out.write(b,0,n);}
   Pack.importPack(pack,library,null);check(library.all().size()==4,"initial import");
   Pack.Record r=library.all().get(0);long shelf=library.createShelf("手机新增");library.assign(shelf,Arrays.asList(r.id));
   library.edit(r.id,"本机标签",true);
   Pack.importPack(pack,library,null);Pack.Record again=library.get(r.id);
   check(again.shelves.contains("手机新增"),"same id reimport must preserve phone collection");
   check(again.tags.equals("本机标签")&&again.starred==1,"local tags/star survive");
   check(library.all().size()==4,"no duplicates");
   File object=library.objectPath(r.sha);try(RandomAccessFile f=new RandomAccessFile(object,"rw")){int value=f.read();f.seek(0);f.write(value^1);}
   Pack.importPack(pack,library,null);Pack.exportPack(new File(isolated,"repaired.vaultpack"),library,null);
   check(true,"corrupt local object repaired, export hashes match");
   Pack.Record custom=new Pack.Record();custom.id="abcdefabcdefabcdefabcdefabcdefab";custom.sha=r.sha;custom.name="incoming rename";custom.kind=r.kind;custom.tags="incoming";custom.shelves.add("新包分类");
   library.beginImport();library.put(custom);library.commitImport();again=library.get(r.id);
   check(again.tags.equals("本机标签")&&again.starred==1,"different id same content preserves local edits");
   check(again.shelves.contains("手机新增")&&again.shelves.contains("新包分类"),"collections union");
   library.beginImport();library.edit(r.id,"事务中修改",false);library.assign(library.ensureShelf("不应保留"),Arrays.asList(r.id));library.rollbackImport();
   again=library.get(r.id);check(again.tags.equals("本机标签")&&again.starred==1&&!again.shelves.contains("不应保留"),"transaction rollback is atomic");
   check(library.subset(Arrays.asList(r.id)).all().size()==1,"selected export store");
   File kept=library.objectPath(r.sha);library.remove(Arrays.asList(r.id));check(kept.isFile()&&library.get(r.id)==null,"trash keeps content");
   String trashId=(String)library.trashRows().get(0).get("id");library.restoreTrash(trashId);check(library.get(r.id).shelves.contains("手机新增"),"trash restores collection");
   library.remove(Arrays.asList(r.id));trashId=(String)library.trashRows().get(0).get("id");library.purgeTrash(trashId);check(!kept.exists(),"permanent purge removes only unreferenced object");
   result.putString("stream","PASS "+passed+" Android SQLite/import checks\n");finish(-1,result);
  }catch(Throwable error){result.putString("stream","FAIL "+error.toString()+"\n");finish(0,result);}
  finally{if(library!=null)library.close();}
 }
}
