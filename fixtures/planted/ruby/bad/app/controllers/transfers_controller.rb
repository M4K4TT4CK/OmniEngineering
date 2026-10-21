# frozen_string_literal: true

# CWE-352: a cookie-session controller that skips Rails' CSRF check "so the form works".
class TransfersController < ApplicationController
  skip_before_action :verify_authenticity_token # gap: CWE-352 - nothing in this build reports it (docs/cwe-top25.md)

  def create
    current_user.account.transfer_to(params.require(:to), params.require(:amount).to_i)
    redirect_to account_path
  end
end
