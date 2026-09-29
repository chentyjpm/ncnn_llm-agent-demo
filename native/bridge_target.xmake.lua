-- Place ncnn_agent_bridge.cpp in examples/, then append this block to the existing
-- ncnn_llm/xmake.lua only after reviewing/backing up it. Alternative to our CMake.
target("ncnn_agent_bridge")
    set_kind("binary")
    add_files("examples/ncnn_agent_bridge.cpp")
    add_deps("ncnn_llm")
    add_packages("ncnn", "nlohmann_json")
    if is_plat("windows", "mingw") then
        add_syslinks("shell32")
    end
    set_rundir("$(projectdir)/")
