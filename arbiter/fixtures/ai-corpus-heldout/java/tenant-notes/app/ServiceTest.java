package tenantnotes;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.sql.SQLException;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.function.Executable;
import tenantnotes.Service.Note;
import tenantnotes.Service.Role;
import tenantnotes.Service.ServiceError;
import tenantnotes.Service.User;

class ServiceTest {

    private static final String PASSWORD = "password123";

    private Service service;

    @BeforeEach
    void setUp() throws SQLException {
        service = new Service("jdbc:sqlite::memory:");
    }

    @AfterEach
    void tearDown() throws SQLException {
        service.close();
    }

    @Test
    void adminCanCreateUpdateAndDeleteNotes() {
        User admin = createAcme();
        Note note = service.createNote(admin, "Plan", "Ship it");

        Note updated = service.updateNote(admin, note.id(), "Plan v2", null);

        assertEquals("Plan v2", updated.title());
        assertEquals("Ship it", updated.body());
        service.deleteNote(admin, note.id());
        assertStatus(404, () -> service.getNote(admin, note.id()));
    }

    @Test
    void editorCanWriteButViewerCanOnlyRead() {
        User admin = createAcme();
        User editor = addUser(admin, "editor@acme.test", Role.EDITOR);
        User viewer = addUser(admin, "viewer@acme.test", Role.VIEWER);
        Note note = service.createNote(editor, "Hello", "World");

        assertEquals(note.id(), service.getNote(viewer, note.id()).id());
        assertEquals(1, service.listNotes(viewer).size());
        assertStatus(403, () -> service.createNote(viewer, "x", "y"));
        assertStatus(403, () -> service.updateNote(viewer, note.id(), "x", null));
        assertStatus(403, () -> service.deleteNote(viewer, note.id()));
    }

    @Test
    void onlyAdminsCanInviteOrChangeRoles() {
        User admin = createAcme();
        User editor = addUser(admin, "editor@acme.test", Role.EDITOR);
        assertStatus(403, () -> service.invite(editor, "x@acme.test", Role.VIEWER));
        assertStatus(403, () -> service.changeRole(editor, admin.id(), Role.VIEWER));
    }

    @Test
    void notesAndUsersAreIsolatedBetweenOrganisations() {
        User acme = createAcme();
        User acmeEditor = addUser(acme, "editor@acme.test", Role.EDITOR);
        User globex = service.createOrganisation("Globex", "admin@globex.test", PASSWORD);
        Note note = service.createNote(acme, "Secret", "acme only");

        assertStatus(404, () -> service.getNote(globex, note.id()));
        assertStatus(404, () -> service.updateNote(globex, note.id(), "hijack", null));
        assertStatus(404, () -> service.deleteNote(globex, note.id()));
        assertStatus(404, () -> service.changeRole(globex, acmeEditor.id(), Role.ADMIN));
        assertTrue(service.listNotes(globex).isEmpty());
        assertTrue(service.searchNotes(globex, "secret").isEmpty());
    }

    @Test
    void searchMatchesTitleAndBodyCaseInsensitively() {
        User admin = createAcme();
        service.createNote(admin, "Meeting notes", "Discuss roadmap");
        service.createNote(admin, "Groceries", "milk; 100% fat-free");
        service.createNote(admin, "Other", "nothing");

        assertEquals(1, service.searchNotes(admin, "MEETING").size());
        assertEquals(1, service.searchNotes(admin, "roadmap").size());
        assertEquals(1, service.searchNotes(admin, "%").size());
        assertEquals(0, service.searchNotes(admin, "absent").size());
    }

    @Test
    void adminCanChangeRolesButNotDemoteTheLastAdmin() {
        User admin = createAcme();
        User editor = addUser(admin, "editor@acme.test", Role.EDITOR);

        service.changeRole(admin, editor.id(), Role.VIEWER);

        assertEquals(Role.VIEWER, service.authenticate(service.login("editor@acme.test", PASSWORD)).role());
        assertStatus(409, () -> service.changeRole(admin, admin.id(), Role.EDITOR));
    }

    @Test
    void loginRejectsWrongPassword() {
        createAcme();
        assertStatus(401, () -> service.login("admin@acme.test", "wrong-password"));
    }

    @Test
    void unknownTokenIsRejected() {
        assertStatus(401, () -> service.authenticate("not-a-token"));
    }

    @Test
    void invitationIsSingleUseAndEmailMustBeNew() {
        User admin = createAcme();
        String invitation = service.invite(admin, "new@acme.test", Role.EDITOR);

        service.acceptInvitation(invitation, PASSWORD);

        assertStatus(404, () -> service.acceptInvitation(invitation, PASSWORD));
        assertStatus(409, () -> service.invite(admin, "admin@acme.test", Role.VIEWER));
    }

    @Test
    void emailsAreUniqueAcrossOrganisations() {
        createAcme();
        assertStatus(409, () -> service.createOrganisation("Globex", "ADMIN@acme.test", PASSWORD));
    }

    @Test
    void shortPasswordsAreRejected() {
        assertStatus(400, () -> service.createOrganisation("Globex", "admin@globex.test", "short"));
    }

    private User createAcme() {
        return service.createOrganisation("Acme", "admin@acme.test", PASSWORD);
    }

    private User addUser(User admin, String email, Role role) {
        String invitation = service.invite(admin, email, role);
        return service.authenticate(service.acceptInvitation(invitation, PASSWORD));
    }

    private static void assertStatus(int status, Executable action) {
        assertEquals(status, assertThrows(ServiceError.class, action).status());
    }
}
