package planted;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.jsontype.impl.LaissezFaireSubTypeValidator;

/** Polymorphic JSON for job payloads. */
public class JsonCodec {
    public ObjectMapper legacyMapper() {
        ObjectMapper mapper = new ObjectMapper();
        mapper.enableDefaultTyping(); // expect: arbiter/java.jackson-default-typing
        return mapper;
    }

    public ObjectMapper mapper() {
        ObjectMapper mapper = new ObjectMapper();
        mapper.activateDefaultTyping(LaissezFaireSubTypeValidator.instance, ObjectMapper.DefaultTyping.NON_FINAL); // expect: arbiter/java.jackson-default-typing
        return mapper;
    }
}
