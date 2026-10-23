# SMTP delivery settings as model-written job handlers build them (ARB-083).
module MailSettings
  def self.smtp
    {
      address: ENV.fetch("SMTP_ADDRESS"),
      port: Integer(ENV.fetch("SMTP_PORT", "587")),
      user_name: ENV["SMTP_USER"],
      password: ENV["SMTP_PASSWORD"],
      openssl_verify_mode: "none" # expect: arbiter/ruby.mail-tls-verify-none
    }
  end
end
