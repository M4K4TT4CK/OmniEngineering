package planted;

import java.io.ByteArrayInputStream;
import java.io.ObjectInputStream;

public class Load {
    Object load(byte[] b) throws Exception {
        try (ObjectInputStream in = new ObjectInputStream(new ByteArrayInputStream(b))) {
            return in.readObject(); // expect: arbiter/java.object-input-stream
        }
    }
}
