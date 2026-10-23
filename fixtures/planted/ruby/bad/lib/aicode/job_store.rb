# An in-memory job store as model-written queues keep it (ARB-083).
require "securerandom"

class JobStore
  Job = Struct.new(:id, :status)

  def initialize
    @mutex = Mutex.new
    @jobs = {}
  end

  def submit
    job = Job.new(SecureRandom.uuid, "queued")
    @mutex.synchronize { @jobs[job.id] = job } # expect: arbiter/aiapp.store-never-evicted
    job
  end

  def get(id)
    @mutex.synchronize { @jobs[id] }
  end
end
