package fr.kerherve.jarvis;

import static org.junit.Assert.assertArrayEquals;
import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertNotEquals;
import static org.junit.Assert.assertNull;
import static org.junit.Assert.assertTrue;
import static org.junit.Assert.fail;

import java.security.KeyPair;

import org.junit.Test;

/** v1.15 (fiche R3) : appairage chiffré téléphone → Adria TV (tests AND-01 et suivants, hors Android). */
public class PairCryptoTest {

    private static final String CFG = "{\"secret\":\"123456\",\"cfg\":{\"token\":\"eyJ-jeton-HA\"}}";

    @Test public void allerRetourChiffre() throws Exception {
        KeyPair tv = PairCrypto.nouvellePaire();
        String[] env = PairCrypto.chiffrer(tv.getPublic().getEncoded(), CFG);
        assertFalse("le jeton ne doit pas apparaître dans l'envoi", String.join("", env).contains("eyJ"));
        assertEquals(CFG, PairCrypto.dechiffrer(tv, env[0], env[1], env[2]));
    }

    @Test public void messageAltereRefuse() throws Exception {
        KeyPair tv = PairCrypto.nouvellePaire();
        String[] env = PairCrypto.chiffrer(tv.getPublic().getEncoded(), CFG);
        byte[] ct = PairCrypto.deB64(env[2]);
        ct[0] ^= 1;
        try { PairCrypto.dechiffrer(tv, env[0], env[1], PairCrypto.b64(ct)); fail("altération acceptée"); }
        catch (Exception attendu) { /* AES-GCM refuse */ }
    }

    @Test public void autreTvNePeutPasLire() throws Exception {
        KeyPair vraie = PairCrypto.nouvellePaire(), fausse = PairCrypto.nouvellePaire();
        String[] env = PairCrypto.chiffrer(vraie.getPublic().getEncoded(), CFG);
        try { PairCrypto.dechiffrer(fausse, env[0], env[1], env[2]); fail("fausse TV a déchiffré"); }
        catch (Exception attendu) { }
    }

    /** AND-01 : une TV dont la clé ne correspond pas au code est refusée avant tout envoi. */
    @Test public void empreinteDistingueLesTv() throws Exception {
        byte[] a = PairCrypto.nouvellePaire().getPublic().getEncoded();
        byte[] b = PairCrypto.nouvellePaire().getPublic().getEncoded();
        assertEquals(8, PairCrypto.empreinte(a).length());
        assertEquals(PairCrypto.empreinte(a), PairCrypto.empreinte(a));
        assertNotEquals(PairCrypto.empreinte(a), PairCrypto.empreinte(b));
    }

    @Test public void lectureDuCode() throws Exception {
        byte[] pub = PairCrypto.nouvellePaire().getPublic().getEncoded();
        String code = PairCrypto.codeAffiche(pub, "042317");
        assertTrue(code.matches("[0-9A-Z]{4}-[0-9A-Z]{4}-[0-9]{6}"));
        String[] lu = PairCrypto.lireCode(" " + code.toLowerCase().replace("-", " ") + " ");
        assertArrayEquals(new String[]{PairCrypto.empreinte(pub), "042317"}, lu);
        assertNull(PairCrypto.lireCode("1234"));            // ancien code à 4 chiffres : refusé
        assertNull(PairCrypto.lireCode("ABCD-EFGH-12345")); // trop court
        assertNull(PairCrypto.lireCode("ABCU-EFGH-123456")); // U hors alphabet
    }

    @Test public void comparaisonTempsConstant() {
        assertTrue(PairCrypto.egal("123456", "123456"));
        assertFalse(PairCrypto.egal("123456", "123457"));
        assertFalse(PairCrypto.egal(null, "1"));
    }
}
