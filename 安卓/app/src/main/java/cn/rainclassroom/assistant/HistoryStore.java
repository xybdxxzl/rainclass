package cn.rainclassroom.assistant;

import android.content.*;
import android.database.Cursor;
import android.database.sqlite.*;
import org.json.*;

/** 题目在接收时落库，失败和取消记录也保存；账号始终参与查询。 */
public final class HistoryStore extends SQLiteOpenHelper {
    public HistoryStore(Context context){super(context.getApplicationContext(),"question_history.db",null,1);}
    @Override public void onCreate(SQLiteDatabase db){db.execSQL("CREATE TABLE questions (id TEXT PRIMARY KEY, account TEXT NOT NULL, received INTEGER NOT NULL, course TEXT NOT NULL, state TEXT NOT NULL, payload TEXT NOT NULL)");db.execSQL("CREATE INDEX account_received ON questions(account,received DESC)");}
    @Override public void onUpgrade(SQLiteDatabase db,int oldVersion,int newVersion){}
    public static JSONObject snapshot(ReviewRegistry.Review r){
        synchronized(r){try{return new JSONObject().put("id",r.historyKey()).put("account",r.account).put("lessonId",r.lessonId).put("course",r.course)
            .put("problemId",r.problemId).put("type",r.type).put("received",r.receivedAt).put("problem",r.problem)
            .put("answers",ReviewRegistry.copy(r.answers)).put("suggestions",ReviewRegistry.copy(r.suggestions)).put("modelState",r.modelState)
            .put("modelMessage",r.modelMessage).put("modelDuration",r.modelDuration).put("state",r.submitState);}catch(JSONException e){throw new IllegalStateException(e);}}
    }
    public synchronized void save(ReviewRegistry.Review r){JSONObject value=snapshot(r);ContentValues row=new ContentValues();row.put("id",value.optString("id"));row.put("account",r.account);row.put("received",r.receivedAt);row.put("course",r.course);row.put("state",value.optString("state"));row.put("payload",value.toString());getWritableDatabase().insertWithOnConflict("questions",null,row,SQLiteDatabase.CONFLICT_REPLACE);}
    /** 在创建新待答记录之前读取；读失败必须交给调用方停止重派，不能当作没有历史。 */
    public synchronized JSONObject get(String account,String lessonId,String problemId){
        String key=account+":"+lessonId+":"+problemId;
        try(Cursor c=getReadableDatabase().query("questions",new String[]{"payload","state"},"id=? AND account=?",
                new String[]{key,account},null,null,null)){
            if(!c.moveToFirst())return null;
            try{
                JSONObject value=new JSONObject(c.getString(0));
                if(!account.equals(value.optString("account"))||!lessonId.equals(value.optString("lessonId"))||!problemId.equals(value.optString("problemId")))
                    throw new SQLiteException("历史题目身份信息异常");
                value.put("state",c.getString(1));return value;
            }catch(JSONException e){throw new SQLiteException("历史题目格式异常");}
        }
    }
    public synchronized JSONArray list(String account,String keyword,String course,String state,String date){
        JSONArray out=new JSONArray();String sql="account=?";java.util.List<String> args=new java.util.ArrayList<>();args.add(account);
        if(course!=null&&!course.trim().isEmpty()){sql+=" AND instr(course,?)>0";args.add(course.trim());}
        if(state!=null&&!state.isEmpty()&&!"全部".equals(state)){sql+=" AND state=?";args.add(state);}
        try(Cursor c=getReadableDatabase().query("questions",new String[]{"payload"},sql,args.toArray(new String[0]),null,null,"received DESC")){
            java.text.SimpleDateFormat format=new java.text.SimpleDateFormat("yyyy-MM-dd",java.util.Locale.ROOT);
            while(c.moveToNext()){try{JSONObject p=new JSONObject(c.getString(0));if(date!=null&&!date.trim().isEmpty()&&!format.format(new java.util.Date(p.optLong("received"))).equals(date.trim()))continue;
                if(keyword!=null&&!keyword.trim().isEmpty()&&!p.toString().toLowerCase(java.util.Locale.ROOT).contains(keyword.trim().toLowerCase(java.util.Locale.ROOT)))continue;out.put(p);}catch(JSONException ignored){}}
        }return out;
    }
    public synchronized void clear(String account){getWritableDatabase().delete("questions","account=?",new String[]{account});}
}

