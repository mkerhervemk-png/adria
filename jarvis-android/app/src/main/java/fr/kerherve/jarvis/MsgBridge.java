package fr.kerherve.jarvis;

import android.content.ComponentName;
import android.content.Context;
import android.content.Intent;

/**
 * Pont entre les applis Adria (même signature) :
 *  - Adria → Adria Messages : messages à lire / auxquels répondre à la voix dans Android Auto ;
 *  - Adria → Adria Hologramme : configuration et option « afficher l'avatar ».
 * La configuration (adresse, jeton, qui parle) est recopiée à chaque envoi.
 */
public final class MsgBridge {

    public static final String PKG = "fr.kerherve.jarvis.messages";
    public static final String HOLO = "fr.kerherve.jarvis.holo";
    public static final String ACTION = "fr.kerherve.jarvis.BRIDGE_MSG";

    public static boolean installed(Context c) { return installed(c, PKG); }

    public static boolean installed(Context c, String pkg) {
        try { c.getPackageManager().getPackageInfo(pkg, 0); return true; }
        catch (Exception e) { return false; }
    }

    /** Vers Adria Messages. cmd : listen | say | alert | config. */
    public static boolean send(Context c, String cmd, String text) { return send(c, PKG, cmd, text); }

    public static boolean send(Context c, String pkg, String cmd, String text) {
        if (pkg.equals(c.getPackageName()) || !installed(c, pkg)) return false;
        Intent i = new Intent(ACTION)
                .setComponent(new ComponentName(pkg, "fr.kerherve.jarvis.BridgeReceiver"))
                .addFlags(Intent.FLAG_INCLUDE_STOPPED_PACKAGES)
                .putExtra("cmd", cmd)
                .putExtra("text", text == null ? "" : text)
                .putExtra("cfg", HaClient.prefs(c).getString("cfg_json", "{}"));
        c.sendBroadcast(i);
        return true;
    }

    /** Recopie la configuration dans les deux autres applis (depuis l'appli Adria uniquement). */
    public static void syncConfig(Context c) {
        if (c.getPackageName().endsWith(".messages") || c.getPackageName().endsWith(".holo")) return;
        send(c, PKG, "config", null);
        send(c, HOLO, "config", null);
    }
}
