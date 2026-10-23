package planted.aicode;

import java.util.Map;
import java.util.Properties;

/** SMTP session properties as model-written job handlers build them (ARB-083). */
public final class MailSettings {
    private MailSettings() {}

    public static Properties smtp(Map<String, String> env) {
        Properties props = new Properties();
        props.put("mail.smtp.host", env.get("SMTP_HOST"));
        props.put("mail.smtp.port", env.getOrDefault("SMTP_PORT", "587"));
        props.put("mail.smtp.auth", "true");
        props.put("mail.smtp.starttls.enable", "true"); // expect: arbiter/java.mail-starttls-not-required
        props.put("mail.smtp.ssl.trust", "*"); // expect: arbiter/java.mail-ssl-trust-all
        return props;
    }
}
