# frozen_string_literal: true

# CWE-352: a cookie-session controller that skips Rails' CSRF check "so the form works".
class TransfersController < ApplicationController
  skip_before_action :verify_authenticity_token # expect: arbiter/ruby.csrf-protection-disabled

  def create
    current_user.account.transfer_to(params.require(:to), params.require(:amount).to_i)
    redirect_to account_path
  end
end
