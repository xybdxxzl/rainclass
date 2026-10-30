package cn.rainclassroom.assistant;

/** 限制自动验证频率，避免更换账号时自动重新选中原账号。 */
final class LoginSessionDetector {
    private final String originalCookie;
    private final String savedSession;
    private String lastToken = "";
    private long lastAttempt = Long.MIN_VALUE;
    private boolean cookieChanged;

    LoginSessionDetector(String originalCookie, String savedSession) {
        this.originalCookie = originalCookie == null ? "" : originalCookie;
        this.savedSession = savedSession == null ? "" : savedSession;
    }

    boolean shouldCheck(String token, long now) {
        if (token == null || token.isEmpty()) {
            if (!originalCookie.isEmpty()) cookieChanged = true;
            return false;
        }
        if (!token.equals(originalCookie)) cookieChanged = true;
        if (!cookieChanged && !savedSession.isEmpty() && token.equals(savedSession)) return false;
        if (token.equals(lastToken) && lastAttempt != Long.MIN_VALUE && now - lastAttempt < 10000) return false;
        lastToken = token;
        lastAttempt = now;
        return true;
    }
}
