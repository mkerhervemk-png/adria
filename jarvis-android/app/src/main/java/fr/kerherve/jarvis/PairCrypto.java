package fr.kerherve.jarvis;

import java.nio.charset.StandardCharsets;
import java.security.KeyFactory;
import java.security.KeyPair;
import java.security.KeyPairGenerator;
import java.security.MessageDigest;
import java.security.PrivateKey;
import java.security.PublicKey;
import java.security.SecureRandom;
import java.security.spec.ECGenParameterSpec;
import java.security.spec.X509EncodedKeySpec;
import java.util.Base64;

import javax.crypto.Cipher;
import javax.crypto.KeyAgreement;
import javax.crypto.Mac;
import javax.crypto.spec.GCMParameterSpec;
import javax.crypto.spec.SecretKeySpec;

/**
 * v1.15 (fiche R3) : cryptographie de l'appairage téléphone → Adria TV, en Java pur (testable hors Android).
 *
 * Protocole « v2 » :
 * 1. À chaque ouverture (et après expiration ou 5 échecs), la TV crée une paire de clés EC P-256 éphémère
 *    et un secret à 6 chiffres. Elle affiche le code « EMPR-EINT-SSSSSS » : 8 caractères = empreinte
 *    SHA-256 de sa clé publique (40 bits), puis le secret.
 * 2. Le téléphone lit la clé publique de la TV et vérifie qu'elle correspond à l'empreinte tapée
 *    (équivalent d'une validation de certificat : une fausse TV est refusée avant tout envoi).
 * 3. Le téléphone crée sa propre clé éphémère, fait un ECDH, dérive une clé AES-256 (HKDF-SHA256 liée aux
 *    deux clés publiques) et envoie {secret, cfg} chiffré en AES-GCM (authentifié).
 * 4. La TV déchiffre, vérifie le secret (temps constant), enregistre la configuration chiffrée
 *    (SecureCfg) puis jette ses clés et change de code : appairage à usage unique.
 * Rien ne circule en clair : ni le jeton HA, ni le secret.
 */
public final class PairCrypto {

    static final String ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"; // base32 Crockford (sans I L O U)
    static final byte[] INFO = "adria-tv-pair-v2".getBytes(StandardCharsets.US_ASCII);
    static final int EMPREINTE = 8, SECRET = 6;
    private static final SecureRandom RND = new SecureRandom();

    private PairCrypto() { }

    public static KeyPair nouvellePaire() throws Exception {
        KeyPairGenerator g = KeyPairGenerator.getInstance("EC");
        g.initialize(new ECGenParameterSpec("secp256r1"), RND);
        return g.generateKeyPair();
    }

    public static String secret() {
        StringBuilder b = new StringBuilder();
        for (int i = 0; i < SECRET; i++) b.append((char) ('0' + RND.nextInt(10)));
        return b.toString();
    }

    public static String b64(byte[] b) { return Base64.getEncoder().encodeToString(b); }

    public static byte[] deB64(String s) { return Base64.getDecoder().decode(s); }

    public static PublicKey cle(byte[] x509) throws Exception {
        return KeyFactory.getInstance("EC").generatePublic(new X509EncodedKeySpec(x509));
    }

    /** Empreinte affichée : 8 caractères base32 des 40 premiers bits du SHA-256 de la clé publique. */
    public static String empreinte(byte[] pub) throws Exception {
        byte[] h = MessageDigest.getInstance("SHA-256").digest(pub);
        long v = 0;
        for (int i = 0; i < 5; i++) v = (v << 8) | (h[i] & 0xff);
        char[] c = new char[EMPREINTE];
        for (int i = EMPREINTE - 1; i >= 0; i--) { c[i] = ALPHABET.charAt((int) (v & 31)); v >>>= 5; }
        return new String(c);
    }

    /** Code à afficher sur la TV : « ABCD-EFGH-123456 ». */
    public static String codeAffiche(byte[] pub, String secret) throws Exception {
        String e = empreinte(pub);
        return e.substring(0, 4) + "-" + e.substring(4) + "-" + secret;
    }

    /** Code tapé -> {empreinte, secret}, ou null s'il n'a pas la bonne forme. Tolère espaces, tirets,
     *  minuscules et les confusions O/0, I/L/1. */
    public static String[] lireCode(String tape) {
        if (tape == null) return null;
        String s = tape.toUpperCase().replaceAll("[^0-9A-Z]", "")
                .replace('O', '0').replace('I', '1').replace('L', '1');
        if (s.length() != EMPREINTE + SECRET) return null;
        String e = s.substring(0, EMPREINTE), sec = s.substring(EMPREINTE);
        for (char ch : e.toCharArray()) if (ALPHABET.indexOf(ch) < 0) return null;
        if (!sec.matches("[0-9]{" + SECRET + "}")) return null;
        return new String[]{e, sec};
    }

    public static boolean egal(String a, String b) {
        if (a == null || b == null) return false;
        return MessageDigest.isEqual(a.getBytes(StandardCharsets.UTF_8), b.getBytes(StandardCharsets.UTF_8));
    }

    /** Clé AES-256 commune : HKDF-SHA256(ECDH, sel = SHA-256(pubTV || pubTel), info = INFO). */
    static byte[] cleCommune(PrivateKey moi, PublicKey autre, byte[] pubTv, byte[] pubTel) throws Exception {
        KeyAgreement ka = KeyAgreement.getInstance("ECDH");
        ka.init(moi);
        ka.doPhase(autre, true);
        byte[] z = ka.generateSecret();
        MessageDigest d = MessageDigest.getInstance("SHA-256");
        d.update(pubTv);
        d.update(pubTel);
        byte[] sel = d.digest();
        Mac m = Mac.getInstance("HmacSHA256");
        m.init(new SecretKeySpec(sel, "HmacSHA256"));
        byte[] prk = m.doFinal(z);
        m.init(new SecretKeySpec(prk, "HmacSHA256"));
        m.update(INFO);
        m.update((byte) 1);
        return m.doFinal(); // 32 octets
    }

    /** Côté téléphone : {pub, iv, ct} (base64) à envoyer à la TV. */
    public static String[] chiffrer(byte[] pubTv, String clair) throws Exception {
        KeyPair tel = nouvellePaire();
        byte[] pubTel = tel.getPublic().getEncoded();
        byte[] k = cleCommune(tel.getPrivate(), cle(pubTv), pubTv, pubTel);
        byte[] iv = new byte[12];
        RND.nextBytes(iv);
        Cipher c = Cipher.getInstance("AES/GCM/NoPadding");
        c.init(Cipher.ENCRYPT_MODE, new SecretKeySpec(k, "AES"), new GCMParameterSpec(128, iv));
        c.updateAAD(INFO);
        byte[] ct = c.doFinal(clair.getBytes(StandardCharsets.UTF_8));
        return new String[]{b64(pubTel), b64(iv), b64(ct)};
    }

    /** Côté TV : texte clair, ou exception si la clé, l'IV ou le message ont été altérés. */
    public static String dechiffrer(KeyPair tv, String pubTelB64, String ivB64, String ctB64) throws Exception {
        byte[] pubTv = tv.getPublic().getEncoded();
        byte[] pubTel = deB64(pubTelB64);
        byte[] k = cleCommune(tv.getPrivate(), cle(pubTel), pubTv, pubTel);
        byte[] iv = deB64(ivB64);
        if (iv.length != 12) throw new IllegalArgumentException("iv");
        Cipher c = Cipher.getInstance("AES/GCM/NoPadding");
        c.init(Cipher.DECRYPT_MODE, new SecretKeySpec(k, "AES"), new GCMParameterSpec(128, iv));
        c.updateAAD(INFO);
        return new String(c.doFinal(deB64(ctB64)), StandardCharsets.UTF_8);
    }
}
