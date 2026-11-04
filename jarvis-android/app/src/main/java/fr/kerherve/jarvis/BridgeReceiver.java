package fr.kerherve.jarvis;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;

/** (Adria Messages) Reçoit de l'appli Adria la configuration et les messages à présenter dans Android Auto. */
public class BridgeReceiver extends BroadcastReceiver {
    @Override
    public void onReceive(Context ctx, Intent in) {
        Context c = ctx.getApplicationContext();
        String cfg = in.getStringExtra("cfg");
        if (cfg != null && cfg.startsWith("{") && cfg.length() > 2)
            HaClient.prefs(c).edit().putString("cfg_json", cfg).apply();
        Notifs.channels(c);
        String cmd = String.valueOf(in.getStringExtra("cmd"));
        String text = in.getStringExtra("text");
        if (text == null || text.isEmpty()) return;
        if (cmd.equals("alert")) Notifs.alert(c, text, null);
        else if (cmd.equals("listen") || cmd.equals("say")) Notifs.adriaSays(c, text);
    }
}
