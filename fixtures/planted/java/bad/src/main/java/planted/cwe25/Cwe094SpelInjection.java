package planted.cwe25;

import org.springframework.expression.ExpressionParser;
import org.springframework.expression.spel.standard.SpelExpressionParser;
import org.springframework.expression.spel.support.StandardEvaluationContext;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RestController;

/** CWE-94: a "dynamic pricing rule" evaluated as SpEL, so T(java.lang.Runtime) is reachable. */
@RestController
public class Cwe094SpelInjection {
    private final ExpressionParser parser = new SpelExpressionParser();

    @PostMapping("/pricing/preview") // expect-ext: semgrep/semgrep-rules.java.spring.security.audit.spel-injection
    public Object preview(@RequestBody String rule) {
        return parser.parseExpression(rule).getValue(new StandardEvaluationContext(new Cart()));
    }

    public record Cart(double subtotal, int items) {
        public Cart() { this(0, 0); }
    }
}
