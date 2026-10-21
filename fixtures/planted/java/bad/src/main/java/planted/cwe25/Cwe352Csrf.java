package planted.cwe25;

import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.security.config.annotation.web.builders.HttpSecurity;
import org.springframework.security.web.SecurityFilterChain;

/** CWE-352: a session-cookie web app with Spring Security's CSRF protection turned off. */
@Configuration
public class Cwe352Csrf {
    @Bean
    public SecurityFilterChain filterChain(HttpSecurity http) throws Exception {
        http.csrf(csrf -> csrf.disable()) // gap: CWE-352 - nothing in this build reports it (docs/cwe-top25.md)
            .authorizeHttpRequests(auth -> auth.anyRequest().authenticated())
            .formLogin(form -> form.permitAll());
        return http.build();
    }
}
