package fr.kerherve.jarvis.car;

import android.content.Context;
import android.graphics.Bitmap;
import android.os.Handler;
import android.os.HandlerThread;

import fr.kerherve.jarvis.HaClient;
import fr.kerherve.jarvis.Notifs;

import org.json.JSONObject;

/**
 * Surveille les verdicts caméra d'ADRIA pendant que Jarvis est ouvert dans la voiture.
 * Nouveau verdict « personne / véhicule » (hors passants dans la rue) → image de la caméra + alerte.
 * Aucun appel IA : simple lecture d'état HA toutes les 10 s.
 */
public final class CourWatcher {

    public interface Listener { void onAlert(String titre, String detail, String camera, Bitmap img); }

    /** {entité verdict, caméra, nom} — la cour par défaut, l'intérieur en option. */
    static final String[][] COUR = {{"input_text.securite_cour_dernier_verdict", "camera.cameras_de_la_cour", "Cour"}};
    static final String[][] INTERIEUR = {
            {"input_text.securite_salon_dernier_verdict", "camera.camera_salon", "Salon"},
            {"input_text.securite_cuisine_dernier_verdict", "camera.camera_cuisine", "Cuisine"},
            {"input_text.securite_salle_dernier_verdict", "camera.camera_salle", "Salle"}};

    private static final long PERIOD_MS = 10_000;

    private final Context ctx;
    private final Listener listener;
    private HandlerThread thread;
    private Handler h;
    private final java.util.Map<String, String> last = new java.util.HashMap<>();

    public CourWatcher(Context c, Listener l) { this.ctx = c.getApplicationContext(); this.listener = l; }

    static boolean interieur(Context c) {
        try { return new JSONObject(HaClient.prefs(c).getString("cfg_json", "{}")).optBoolean("alertIn", false); }
        catch (Exception e) { return false; }
    }

    /** Verdict qui mérite une alerte : personne ou véhicule confirmé, pas un passant dans la rue. */
    static boolean important(String v) {
        String s = v.toUpperCase();
        if (s.contains("DANS LA RUE") || s.contains("FAUX") || s.contains("AUCUN") || s.contains("RAS")) return false;
        return s.contains("PERSONNE") || s.contains("VEHICULE") || s.contains("VÉHICULE") || s.contains("VOITURE") || s.contains("INTRUS");
    }

    public void start() {
        if (thread != null) return;
        thread = new HandlerThread("jarvis-cour");
        thread.start();
        h = new Handler(thread.getLooper());
        h.post(this::tick);
    }

    public void stop() {
        if (thread != null) { thread.quitSafely(); thread = null; h = null; }
    }

    private void tick() {
        HaClient ha = HaClient.from(ctx);
        if (ha.configured()) {
            check(ha, COUR);
            if (interieur(ctx)) check(ha, INTERIEUR);
        }
        Handler hh = h;
        if (hh != null) hh.postDelayed(this::tick, PERIOD_MS);
    }

    private void check(HaClient ha, String[][] list) {
        for (String[] w : list) {
            try {
                String v = ha.state(w[0]);
                String prev = last.put(w[0], v);
                if (prev == null || prev.equals(v) || !important(v)) continue; // 1re lecture = référence
                Bitmap img = null;
                try { img = ha.cameraImage(w[1]); } catch (Exception ignored) { }
                String detail = v.replaceFirst("^\\d{1,2}:\\d{2}\\s*", "");
                String titre = w[2] + " — " + (detail.contains("—") ? detail.substring(0, detail.indexOf('—')).trim() : "Événement");
                String desc = detail.contains("—") ? detail.substring(detail.indexOf('—') + 1).trim() : detail;
                titre = titre.replace("PERSONNE_CONFIRMEE", "personne confirmée").replace('_', ' ');
                Notifs.alert(ctx, titre + ". " + desc, img);
                listener.onAlert(titre, desc, w[1], img);
            } catch (Exception ignored) { /* HA injoignable : on réessaie au prochain passage */ }
        }
    }
}
