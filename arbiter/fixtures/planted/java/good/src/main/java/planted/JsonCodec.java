package planted;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.jsontype.BasicPolymorphicTypeValidator;
import com.fasterxml.jackson.databind.jsontype.PolymorphicTypeValidator;

/** Polymorphic JSON for job payloads, limited to our own payload types. */
public class JsonCodec {
    public ObjectMapper mapper() {
        PolymorphicTypeValidator ptv = BasicPolymorphicTypeValidator.builder()
                .allowIfSubType("planted.payload.")
                .build();
        ObjectMapper mapper = new ObjectMapper();
        mapper.activateDefaultTyping(ptv, ObjectMapper.DefaultTyping.NON_FINAL);
        return mapper;
    }
}
