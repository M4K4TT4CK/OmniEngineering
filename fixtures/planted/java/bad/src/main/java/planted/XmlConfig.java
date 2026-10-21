package planted;

import java.beans.XMLDecoder;
import java.io.InputStream;

/** Reads a serialized configuration bean. */
public class XmlConfig {
    public Object read(InputStream in) {
        try (XMLDecoder decoder = new XMLDecoder(in)) {
            return decoder.readObject(); // expect: arbiter/java.xml-decoder
        }
    }
}
