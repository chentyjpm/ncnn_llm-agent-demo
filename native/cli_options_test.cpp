#include "cli_options.h"
#include <iostream>
#include <vector>

int main() {
    struct Case { const char* value; int minimum; int maximum; bool valid; int expected; };
    const std::vector<Case> cases = {
        {"1", 1, 128, true, 1}, {"4", 1, 128, true, 4}, {"128", 1, 128, true, 128},
        {"0", 0, std::numeric_limits<int>::max(), true, 0},
        {"2147483647", 0, std::numeric_limits<int>::max(), true, 2147483647},
        {"0", 1, 128, false, 0}, {"129", 1, 128, false, 0}, {"-1", 1, 128, false, 0},
        {"-1", 0, std::numeric_limits<int>::max(), false, 0},
        {"", 1, 128, false, 0}, {"abc", 1, 128, false, 0}, {"4junk", 1, 128, false, 0},
        {"4.0", 1, 128, false, 0}, {" 4", 1, 128, false, 0}, {"4 ", 1, 128, false, 0},
        {"+4", 1, 128, false, 0}, {"1e2", 1, 128, false, 0},
        {"2147483648", 0, std::numeric_limits<int>::max(), false, 0},
        {"999999999999999999999999", 1, 128, false, 0}, {"-2147483649", 1, 128, false, 0}
    };
    unsigned failures = 0;
    for (const auto& c : cases) {
        try {
            const int got = parse_cli_integer(c.value, "--test", c.minimum, c.maximum);
            if (!c.valid || got != c.expected) {
                std::cerr << "Unexpected accepted value: " << c.value << '\n'; ++failures;
            }
        } catch (const std::runtime_error& e) {
            if (c.valid || std::string(e.what()).find("Invalid value for --test") == std::string::npos) {
                std::cerr << "Unexpected rejection: " << c.value << ": " << e.what() << '\n'; ++failures;
            }
        }
    }
    // Do not use assert: Release builds define NDEBUG.
    std::cout << "Native parser cases: " << cases.size() << "; failures: " << failures << '\n';
    return failures ? 1 : 0;
}
