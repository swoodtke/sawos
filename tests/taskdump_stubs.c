/* design 158 unit 3: the thread, mutex and condition-variable stand-ins
 * `taskdump.saw` needs.
 *
 * `TaskGroup.deinit` reaches the multi-threaded path on EVERY group,
 * single-threaded ones included, so a freestanding link has to resolve the
 * worker pool's names even though nothing in that test ever takes the branch.
 * SOS is a uniprocessor kernel with no threads: a spawn here would be a lie, so
 * it hands back a handle no join ever waits on, and none is ever asked for.
 *
 * The two condition-variable names arrived with design 225, which made the pool
 * LIVE and gave its idle workers a real park to sleep on — so the set a
 * freestanding image has to resolve grew by exactly the primitives that park
 * and wake. Same stand-in discipline: they are never called here.
 *
 * This is C rather than Saw because `__saw_rt_thread_spawn` takes a raw C
 * function pointer, which Saw cannot express — DF-113b, the same gap that keeps
 * the hosted runtime's copy of this seam in `sawc/rt/shim.c`.
 *
 * THIS TEST'S ONLY. It is deliberately not in `sos/rt/common_c/support.c`,
 * which every image links: defining these there would satisfy a real kernel's
 * accidental reference to a thread facility that does not exist, which is a
 * link error worth keeping.
 */

typedef long saw_word;

saw_word __saw_rt_thread_spawn(void *(*entry)(void *), char *env)
{
    (void)entry;
    (void)env;
    return 0;
}

void __saw_rt_thread_join(saw_word handle)
{
    (void)handle;
}

saw_word pthread_mutex_lock(void *mutex)
{
    (void)mutex;
    return 0;
}

saw_word pthread_mutex_unlock(void *mutex)
{
    (void)mutex;
    return 0;
}

saw_word pthread_cond_wait(void *cond, void *mutex)
{
    (void)cond;
    (void)mutex;
    return 0;
}

saw_word pthread_cond_broadcast(void *cond)
{
    (void)cond;
    return 0;
}
