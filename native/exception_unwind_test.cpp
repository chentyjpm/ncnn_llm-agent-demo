// Model-free regression for the MSVC exception-unwinding configuration.
#include <iostream>
#include <stdexcept>
#include <string>

#if defined(_MSC_VER) && !defined(_CPPUNWIND)
#error "Standard C++ exception unwinding must be enabled (/EHsc)."
#endif

struct Guard {
    int& count;
    ~Guard() { ++count; }
};

static void throw_with_guard(int& destroyed) {
    Guard guard{destroyed};
    throw std::runtime_error("expected exception");
}

static void rethrow_with_guard(int& destroyed) {
    Guard guard{destroyed};
    try {
        throw_with_guard(destroyed);
    } catch (const std::runtime_error&) {
        throw;
    }
}

int main() {
    int failures = 0;
    int destroyed = 0;
    bool caught = false;
    try { throw_with_guard(destroyed); }
    catch (const std::runtime_error& e) { caught = std::string(e.what()) == "expected exception"; }
    if (!caught || destroyed != 1) ++failures;

    destroyed = 0;
    caught = false;
    try { rethrow_with_guard(destroyed); }
    catch (const std::runtime_error&) { caught = true; }
    if (!caught || destroyed != 2) ++failures;

    destroyed = 0;
    { Guard guard{destroyed}; }
    if (destroyed != 1) ++failures;

    // Explicit result checks remain active in Release / NDEBUG builds.
    std::cout << "Native unwind cases: 3; failures: " << failures << '\n';
    return failures ? 1 : 0;
}
