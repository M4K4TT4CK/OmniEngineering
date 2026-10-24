package planted.cwe25;

import java.util.Map;
import java.util.function.DoubleUnaryOperator;

import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.server.ResponseStatusException;

/** CWE-94: pricing rules are chosen by name from a fixed table, never evaluated as code. */
@RestController
public class Cwe094SpelInjection {
    private static final Map<String, DoubleUnaryOperator> RULES = Map.of(
        "none", s -> s,
        "ten-percent-off", s -> s * 0.9,
        "five-off-over-fifty", s -> s > 50 ? s - 5 : s);

    @PostMapping("/pricing/preview")
    public double preview(@RequestBody String rule) {
        DoubleUnaryOperator op = RULES.get(rule.strip());
        if (op == null) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST);
        }
        return op.applyAsDouble(100.0);
    }
}
