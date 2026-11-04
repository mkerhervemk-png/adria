package fr.kerherve.jarvis;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.os.Bundle;

import androidx.core.app.RemoteInput;

import org.json.JSONObject;

/** (Pas de goAsync : ADRIA peut mettre ~60 s, au-delà de la limite d'un broadcast.)
 * Réponse vocale dictée dans Android Auto (ou depuis la notification) → ADRIA → nouvelle réponse lue. */
public class ReplyReceiver extends BroadcastReceiver {

    @Override
    public void onReceive(Context ctx, Intent intent) {
        Context c = ctx.getApplicationContext();
        if (Notifs.ACTION_READ.equals(intent.getAction())) return;
        Bundle b = RemoteInput.getResultsFromIntent(intent);
        CharSequence cs = b != null ? b.getCharSequence(Notifs.KEY_REPLY) : null;
        String text = cs == null ? "" : cs.toString().trim();
        if (text.isEmpty()) return;

        Notifs.userSaid(c, text);
        new Thread(() -> {
            HaClient ha = HaClient.from(c);
            try {
                JSONObject r = ha.ask(text, null);
                Notifs.adriaSays(c, r.optString("texte"));
            } catch (Exception e) {
                Notifs.adriaSays(c, HaClient.explain(e, ha.url));
            }
        }).start();
    }
}
