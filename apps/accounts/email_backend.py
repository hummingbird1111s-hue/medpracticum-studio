from django.core.mail.backends.smtp import EmailBackend
import socket

class CustomEmailBackend(EmailBackend):
    def open(self):
        if self.connection:
            return False
        try:
            # Force a valid local_hostname to prevent Gmail from dropping the connection
            self.connection = self.connection_class(
                self.host, self.port, local_hostname=socket.getfqdn()
            )
            if self.use_ssl:
                self.connection.ehlo()
            if self.use_tls:
                self.connection.starttls()
            if self.username and self.password:
                self.connection.login(self.username, self.password)
            return True
        except Exception:
            if not self.fail_silently:
                raise