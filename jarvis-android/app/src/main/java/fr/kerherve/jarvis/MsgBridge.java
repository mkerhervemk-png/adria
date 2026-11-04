package fr.kerherve.jarvis;

import android.content.ComponentName;
import android.content.Context;
import android.content.Intent;

/**
 * Pont Adria → Adria Messages (appli séparée, même signature).
 * Les messages envoyés ici apparaissent dans Android Auto comme des messages d'ADRIA,
 * auxquels on peut répondre à la voix. La configuration (adresse, jeton, qui parle) est recopiée à chaque envoi.
 */
public final class MsgBridge {

    public static final String PKG = "fr.kerherve.jarvis.messages";
    public static final String ACTION = "fr.kerherve.jarvis.BRIDGE_MSG";

    public static boolean installed(Context c) {
        try { c.getPackageManager().getPackageInfo(PKG, 0); return true; }
        catch (Exception e) { return false; }
    }

    /** cmd : listen | say | alert | config. */
    public static boolean send(Context c, String cmd, String text) {
        if (!installed(c)) return false;
        Intent i = new Intent(ACTION)
                .setComponent(new ComponentName(PKG, "fr.kerherve.jarvis.BridgeReceiver"))
                .addFlags(Intent.FLAG_INCLUDE_STOPPED_PACKAGES)
                .putExtra("cmd", cmd)
                .putExtra("text", text == null ? "" : text)
                .putExtra("cfg", HaClient.prefs(c).getString("cfg_json", "{}"));
        c.sendBroadcast(i);
        return true;
    }
}
