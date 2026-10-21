package planted;

import com.fasterxml.jackson.databind.ObjectMapper;

public class Load {
    private static final ObjectMapper MAPPER = new ObjectMapper();

    Order load(byte[] b) throws Exception {
        return MAPPER.readValue(b, Order.class);
    }

    record Order(String id) {}
}
