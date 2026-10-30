package cn.rainclassroom.assistant;

import android.app.Activity;
import android.graphics.Color;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.widget.Button;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.TextView;

final class Ui {
    static final int BG = Color.rgb(244,247,252), INK = Color.rgb(29,45,70);
    static final int MUTED = Color.rgb(101,120,145), BLUE = Color.rgb(48,100,215);
    private Ui() {}
    static int dp(Activity a, float n) { return (int)(n * a.getResources().getDisplayMetrics().density + .5f); }
    static GradientDrawable bg(int color, int radius, Activity a) {
        GradientDrawable d = new GradientDrawable(); d.setColor(color); d.setCornerRadius(dp(a, radius)); return d;
    }
    static LinearLayout column(Activity a) {
        LinearLayout l = new LinearLayout(a); l.setOrientation(LinearLayout.VERTICAL); return l;
    }
    static LinearLayout card(Activity a) {
        LinearLayout l = column(a); int p=dp(a,18); l.setPadding(p,p,p,p); l.setBackground(bg(Color.WHITE,16,a));
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(-1,-2); lp.bottomMargin=dp(a,12); l.setLayoutParams(lp); return l;
    }
    static TextView text(Activity a, String s, int sp, int color, boolean bold) {
        TextView t=new TextView(a); t.setText(s); t.setTextSize(sp); t.setTextColor(color);
        if(bold)t.setTypeface(Typeface.DEFAULT,Typeface.BOLD); t.setLineSpacing(dp(a,2),1f); return t;
    }
    static void gap(Activity a, LinearLayout l, int height) { View v=new View(a); l.addView(v,new LinearLayout.LayoutParams(1,dp(a,height))); }
    static Button button(Activity a, String s, boolean primary) {
        Button b=new Button(a); b.setText(s); b.setTextSize(14); b.setAllCaps(false); b.setMinHeight(dp(a,48));
        b.setTextColor(primary ? Color.WHITE : BLUE); b.setBackground(bg(primary ? BLUE : Color.rgb(233,240,253),10,a));
        return b;
    }
    static EditText input(Activity a, String hint, boolean secret) {
        EditText e=new EditText(a); e.setSingleLine(true); e.setTextSize(14); e.setTextColor(INK); e.setHintTextColor(MUTED);
        e.setHint(hint); e.setPadding(dp(a,12),dp(a,10),dp(a,12),dp(a,10)); e.setBackground(bg(Color.rgb(243,247,253),8,a));
        if(secret) e.setInputType(android.text.InputType.TYPE_CLASS_TEXT | android.text.InputType.TYPE_TEXT_VARIATION_PASSWORD);
        return e;
    }
    static void field(Activity a, LinearLayout l, String name, View field) {
        gap(a,l,10); l.addView(text(a,name,13,INK,true)); gap(a,l,5); l.addView(field,new LinearLayout.LayoutParams(-1,-2));
    }
    static void buttonGap(Activity a, LinearLayout l, View button) {
        gap(a,l,10); l.addView(button,new LinearLayout.LayoutParams(-1,dp(a,48)));
    }
    static LinearLayout screen(Activity a, String title, String subtitle) {
        a.getWindow().setStatusBarColor(Color.rgb(244,247,252)); a.getWindow().setNavigationBarColor(Color.rgb(244,247,252));
        a.getWindow().getDecorView().setSystemUiVisibility(View.SYSTEM_UI_FLAG_LIGHT_STATUS_BAR);
        LinearLayout root=column(a); root.setBackgroundColor(BG); int p=dp(a,20); root.setPadding(p,p,p,dp(a,10));
        root.addView(text(a,title,24,INK,true)); gap(a,root,3); root.addView(text(a,subtitle,13,MUTED,false)); gap(a,root,16);
        return root;
    }
}
