package fr.kerherve.jarvis;

import android.content.BroadcastReceiver;
import android.content.ComponentName;
import android.content.Context;
import android.content.Intent;
import android.content.pm.PackageManager;

/** (Adria Messages / Adria Hologramme) Reçoit de l'appli Adria la configuration, les messages et l'option avatar. */
public class BridgeReceiver extends BroadcastReceiver {
    @Override
    public void onReceive(Context ctx, Intent in) {
        Context c = ctx.getApplicationContext();
        String cfg = in.getStringExtra("cfg");
        if (cfg != null && cfg.startsWith("{") && cfg.length() > 2)
            HaClient.prefs(c).edit().putString("cfg_json", cfg).apply();
        String cmd = String.valueOf(in.getStringExtra("cmd"));
        String text = in.getStringExtra("text");
        if (cmd.equals("avatar")) {
            if (!c.getPackageName().endsWith(".holo")) return;
            try {
                c.getPackageManager().setComponentEnabledSetting(
                        new ComponentName(c, ParkedActivity.class),
                        "off".equals(text) ? PackageManager.COMPONENT_ENABLED_STATE_DISABLED
                                           : PackageManager.COMPONENT_ENABLED_STATE_ENABLED,
                        PackageManager.DONT_KILL_APP);
            } catch (Exception ignored) { }
            return;
        }
        if (c.getPackageName().endsWith(".holo")) return;
        Notifs.channels(c);
        if (text == null || text.isEmpty()) return;
        if (cmd.equals("alert")) Notifs.alert(c, text, null);
        else if (cmd.equals("listen") || cmd.equals("say")) Notifs.adriaSays(c, text);
    }
}
