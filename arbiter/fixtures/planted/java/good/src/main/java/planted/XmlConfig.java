package planted;

import java.io.IOException;
import java.io.InputStream;
import java.util.Properties;

/** Reads configuration from a properties XML file. */
public class XmlConfig {
    public Properties read(InputStream in) throws IOException {
        Properties props = new Properties();
        props.loadFromXML(in);
        return props;
    }
}
