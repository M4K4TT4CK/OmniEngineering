# An in-memory job store that forgets finished jobs (ARB-083).
require "securerandom"

class JobStore
  Job = Struct.new(:id, :status)

  def initialize
    @mutex = Mutex.new
    @jobs = {}
  end

  def submit
    job = Job.new(SecureRandom.uuid, "queued")
    @mutex.synchronize { @jobs[job.id] = job }
    job
  end

  def get(id)
    @mutex.synchronize { @jobs[id] }
  end

  def drop_finished
    @mutex.synchronize { @jobs.delete_if { |_, j| j.status == "done" } }
  end
end
