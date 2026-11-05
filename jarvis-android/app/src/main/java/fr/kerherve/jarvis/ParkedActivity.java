package fr.kerherve.jarvis;

/**
 * Adria en plein écran sur l'écran de la voiture, VOITURE GARÉE uniquement
 * (appli « parked » Android Auto, catégorie jeu : Android Auto la ferme dès que la voiture roule).
 * Même interface que le téléphone, mise en page paysage avec l'hologramme animé.
 */
public class ParkedActivity extends MainActivity {
    @Override
    protected String startUrl() { return "file:///android_asset/index.html?car=1"; }

    @Override
    protected int[] fallbackInsets(android.view.View v, int l, int t, int r, int b) {
        // Android Auto garé : si aucune barre n'est signalée, on laisse 7 % à gauche pour la barre latérale
        if (l == 0 && t == 0 && r == 0 && b == 0) l = Math.round(getResources().getDisplayMetrics().widthPixels * 0.07f);
        return new int[]{l, t, r, b};
    }
}
