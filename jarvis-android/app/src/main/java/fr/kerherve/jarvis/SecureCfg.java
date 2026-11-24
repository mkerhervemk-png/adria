package fr.kerherve.jarvis;

import android.content.Context;
import android.content.SharedPreferences;
import android.security.keystore.KeyGenParameterSpec;
import android.security.keystore.KeyProperties;
import android.util.Base64;

import java.nio.charset.StandardCharsets;
import java.security.KeyStore;

import javax.crypto.Cipher;
import javax.crypto.KeyGenerator;
import javax.crypto.SecretKey;
import javax.crypto.spec.GCMParameterSpec;

/**
 * v1.15 (fiche R3) : la configuration (adresse HA, jeton longue durée…) est chiffrée en AES-256-GCM avec une
 * clé de l'Android Keystore, qui ne quitte jamais l'appareil et n'est pas exportable.
 * - Préférence « cfg_enc » = base64(IV 12 octets || texte chiffré + tag).
 * - Migration : l'ancien « cfg_json » en clair (et le « token » de la v1.0) est chiffré puis effacé.
 * - Jamais de jeton dans les journaux : les erreurs ne renvoient que « {} ».
 */
public final class SecureCfg {

    private static final String KS = "AndroidKeyStore";
    private static final String ALIAS = "adria_cfg_v1";
    private static final String ENC = "cfg_enc";
    private static final String OLD = "cfg_json";
    private static final Object LOCK = new Object();

    private SecureCfg() { }

    private static SecretKey key() throws Exception {
        KeyStore ks = KeyStore.getInstance(KS);
        ks.load(null);
        KeyStore.Entry e = ks.getEntry(ALIAS, null);
        if (e instanceof KeyStore.SecretKeyEntry) return ((KeyStore.SecretKeyEntry) e).getSecretKey();
        KeyGenerator g = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, KS);
        g.init(new KeyGenParameterSpec.Builder(ALIAS, KeyProperties.PURPOSE_ENCRYPT | KeyProperties.PURPOSE_DECRYPT)
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                .setKeySize(256)
                .build());
        return g.generateKey();
    }

    private static String encrypt(String clair) throws Exception {
        Cipher c = Cipher.getInstance("AES/GCM/NoPadding");
        c.init(Cipher.ENCRYPT_MODE, key());
        byte[] iv = c.getIV();
        byte[] ct = c.doFinal(clair.getBytes(StandardCharsets.UTF_8));
        byte[] out = new byte[iv.length + ct.length];
        System.arraycopy(iv, 0, out, 0, iv.length);
        System.arraycopy(ct, 0, out, iv.length, ct.length);
        return Base64.encodeToString(out, Base64.NO_WRAP);
    }

    private static String decrypt(String b64) throws Exception {
        byte[] in = Base64.decode(b64, Base64.NO_WRAP);
        Cipher c = Cipher.getInstance("AES/GCM/NoPadding");
        c.init(Cipher.DECRYPT_MODE, key(), new GCMParameterSpec(128, in, 0, 12));
        return new String(c.doFinal(in, 12, in.length - 12), StandardCharsets.UTF_8);
    }

    /** Configuration en JSON ({} si absente ou illisible). */
    public static String get(Context c) {
        synchronized (LOCK) {
            SharedPreferences p = HaClient.prefs(c);
            String enc = p.getString(ENC, null);
            if (enc != null) {
                try { return decrypt(enc); } catch (Exception e) { return "{}"; }
            }
            String old = p.getString(OLD, null);
            if (old != null) {
                put(c, old); // migration ; si le Keystore échoue, l'ancien stockage reste
                return old;
            }
            return "{}";
        }
    }

    /** true si une configuration existe (chiffrée ou ancienne). */
    public static boolean has(Context c) {
        SharedPreferences p = HaClient.prefs(c);
        return p.contains(ENC) || p.contains(OLD);
    }

    /** Enregistre la configuration chiffrée et efface toute copie en clair.
     *  Keystore en panne (très rare) : repli sur l'ancien stockage privé, jamais sauvegardé
     *  (allowBackup=false), pour ne pas perdre la configuration. Renvoie false dans ce cas. */
    public static boolean put(Context c, String json) {
        String v = json == null ? "{}" : json;
        synchronized (LOCK) {
            try {
                String enc = encrypt(v);
                HaClient.prefs(c).edit().putString(ENC, enc).remove(OLD).remove("token").commit();
                return true;
            } catch (Exception e) {
                HaClient.prefs(c).edit().putString(OLD, v).remove(ENC).commit();
                return false;
            }
        }
    }
}
