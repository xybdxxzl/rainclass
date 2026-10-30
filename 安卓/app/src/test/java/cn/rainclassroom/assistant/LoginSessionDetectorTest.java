package cn.rainclassroom.assistant;
import org.junit.Test;
import static org.junit.Assert.*;

public class LoginSessionDetectorTest {
    @Test public void newCookieTriggersValidationAndIsThrottled() {
        LoginSessionDetector d = new LoginSessionDetector("", "");
        assertFalse(d.shouldCheck("", 0));
        assertTrue(d.shouldCheck("new-session", 100));
        assertFalse(d.shouldCheck("new-session", 1000));
        assertTrue(d.shouldCheck("new-session", 10100));
        assertTrue(d.shouldCheck("different-session", 10101));
    }
    @Test public void reauthenticationDoesNotAutomaticallySelectTheOldAccount() {
        LoginSessionDetector d = new LoginSessionDetector("old-session", "old-session");
        assertFalse(d.shouldCheck("old-session", 0));
        assertFalse(d.shouldCheck("old-session", 20000));
        assertTrue(d.shouldCheck("new-session", 20001));
    }
    @Test public void logoutThenSameCookieCanBeValidatedAgain() {
        LoginSessionDetector d = new LoginSessionDetector("old-session", "old-session");
        assertFalse(d.shouldCheck("", 0));
        assertTrue(d.shouldCheck("old-session", 2000));
    }
    @Test public void existingWebCookieCanBeImportedWhenAppHasNoSavedAccount() {
        LoginSessionDetector d = new LoginSessionDetector("web-session", "");
        assertTrue(d.shouldCheck("web-session", 0));
    }
}
