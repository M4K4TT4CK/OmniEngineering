package planted.cwe25;

import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.security.config.annotation.web.builders.HttpSecurity;
import org.springframework.security.config.annotation.web.configurers.AbstractHttpConfigurer;
import org.springframework.security.web.SecurityFilterChain;

/** CWE-352: a session-cookie web app with Spring Security's CSRF protection turned off. */
@Configuration
public class Cwe352Csrf {
    @Bean
    public SecurityFilterChain filterChain(HttpSecurity http) throws Exception {
        http.csrf(csrf -> csrf.disable()) // expect: arbiter/java.csrf-disabled
            .authorizeHttpRequests(auth -> auth.anyRequest().authenticated())
            .formLogin(form -> form.permitAll());
        return http.build();
    }

    @Bean
    public SecurityFilterChain adminChain(HttpSecurity http) throws Exception {
        http.securityMatcher("/admin/**")
            .csrf(AbstractHttpConfigurer::disable) // expect: arbiter/java.csrf-disabled
            .formLogin(form -> form.permitAll());
        return http.build();
    }

    public void legacy(HttpSecurity http) throws Exception {
        http.csrf().disable().formLogin(); // expect: arbiter/java.csrf-disabled expect-ext: semgrep/semgrep-rules.java.spring.security.audit.spring-csrf-disabled
    }
}
