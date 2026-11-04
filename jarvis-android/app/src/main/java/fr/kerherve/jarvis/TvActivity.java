package fr.kerherve.jarvis;

import android.os.Bundle;
import android.view.WindowManager;

/** Adria TV (Fire TV) : hologramme + pages en paysage, navigation à la télécommande, écran toujours allumé. */
public class TvActivity extends MainActivity {
    @Override
    protected void onCreate(Bundle b) {
        super.onCreate(b);
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
    }

    @Override
    protected String startUrl() { return "file:///android_asset/index.html?tv=1"; }
}
