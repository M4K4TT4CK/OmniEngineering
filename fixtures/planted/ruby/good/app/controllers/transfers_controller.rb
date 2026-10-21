# frozen_string_literal: true

# CWE-352: the controller keeps the CSRF check; the form sends authenticity_token.
class TransfersController < ApplicationController
  protect_from_forgery with: :exception

  def create
    current_user.account.transfer_to(params.require(:to), params.require(:amount).to_i)
    redirect_to account_path
  end
end
