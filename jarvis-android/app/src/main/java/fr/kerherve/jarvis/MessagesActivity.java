package fr.kerherve.jarvis;

import android.Manifest;
import android.app.Activity;
import android.content.pm.PackageManager;
import android.graphics.Color;
import android.os.Build;
import android.os.Bundle;
import android.util.TypedValue;
import android.view.Gravity;
import android.widget.Button;
import android.widget.LinearLayout;
import android.widget.TextView;

/** (Adria Messages) Écran minimal : état de la configuration recopiée depuis Adria + message de test. */
public class MessagesActivity extends Activity {

    private int dp(float v) { return (int) TypedValue.applyDimension(TypedValue.COMPLEX_UNIT_DIP, v, getResources().getDisplayMetrics()); }

    @Override
    protected void onCreate(Bundle b) {
        super.onCreate(b);
        Notifs.channels(this);
        if (Build.VERSION.SDK_INT >= 33 && checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED)
            requestPermissions(new String[]{Manifest.permission.POST_NOTIFICATIONS}, 1);

        int bg = Color.parseColor("#050A14");
        getWindow().setStatusBarColor(bg); getWindow().setNavigationBarColor(bg);
        LinearLayout l = new LinearLayout(this);
        l.setOrientation(LinearLayout.VERTICAL); l.setBackgroundColor(bg);
        l.setPadding(dp(24), dp(40), dp(24), dp(24)); l.setGravity(Gravity.CENTER_HORIZONTAL);

        TextView t = new TextView(this); t.setText("Adria Messages"); t.setTextColor(Color.WHITE); t.setTextSize(24);
        TextView d = new TextView(this); d.setTextColor(Color.parseColor("#9FB3C8")); d.setTextSize(15); d.setPadding(0, dp(16), 0, dp(24));
        HaClient ha = HaClient.from(this);
        d.setText((ha.configured()
                ? "✓ Réglages reçus depuis l'appli Adria (" + ha.url.replaceFirst("^https?://", "") + ", " + ha.who + ").\n\n"
                : "⚠ Pas encore de réglages : ouvre l'appli Adria et enregistre ses réglages une fois.\n\n")
                + "Dans la voiture, touche « Parler à ADRIA » sur l'écran Adria : un message d'Adria arrive ici, réponds-lui à la voix.\n\n"
                + "Garde les notifications de cette appli autorisées.");
        Button test = new Button(this); test.setText("Recevoir un message de test");
        test.setOnClickListener(v -> Notifs.adriaSays(this, "Bonjour, je suis Adria. Réponds-moi à la voix."));
        l.addView(t); l.addView(d); l.addView(test);
        setContentView(l);
    }
}
