#pragma once
#include <charconv>
#include <limits>
#include <stdexcept>
#include <string>
#include <system_error>

// std::stoi("4junk") succeeds with 4. CLI integers must consume the entire input.
inline int parse_cli_integer(const std::string& value, const char* option,
                             int minimum, int maximum) {
    int result = 0;
    const auto parsed = std::from_chars(value.data(), value.data() + value.size(), result);
    if (value.empty() || parsed.ec != std::errc() || parsed.ptr != value.data() + value.size()
        || result < minimum || result > maximum) {
        throw std::runtime_error(std::string("Invalid value for ") + option
            + ": expected integer in " + std::to_string(minimum) + ".." + std::to_string(maximum));
    }
    return result;
}
