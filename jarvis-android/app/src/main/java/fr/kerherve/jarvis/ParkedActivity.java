package fr.kerherve.jarvis;

/**
 * Adria en plein écran sur l'écran de la voiture, VOITURE GARÉE uniquement
 * (appli « parked » Android Auto, catégorie jeu : Android Auto la ferme dès que la voiture roule).
 * Même interface que le téléphone, mise en page paysage avec l'hologramme animé.
 */
public class ParkedActivity extends MainActivity {
    @Override
    protected String startUrl() { return "file:///android_asset/index.html?car=1"; }
}
