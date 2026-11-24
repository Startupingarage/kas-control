#include "settings.hpp"
#include <cassert>
#include <condition_variable>
#include <deque>
#include <functional>
#include <iostream>
#include <memory>
#include <thread>

// Test doubles replace network, ALSA and the station agent, never the daemon logic.
inline std::atomic<bool> hold_auth{false},auth_entered{false};
inline json ensure_web_auth(const fs::path &,std::atomic<bool> *) {
    auth_entered=true;while(hold_auth)std::this_thread::sleep_for(std::chrono::milliseconds(1));return json::object();
}
inline std::string indicator_environment_style(){return "rainbow";}
inline bool valid_indicator_style(const std::string &){return true;}
inline void mode_cue_block(bool,int,short *){}
inline std::string indicator_frame(bool,double,const std::string &){return "";}
struct PcmOutput{explicit PcmOutput(const char *){}void write(short *,int){}void drain(){}};
struct Child{int output=-1;void start(std::initializer_list<std::string>,std::initializer_list<std::string>){}void send(const std::string &){}void finish(){}};
struct StationAgent{
    std::mutex metadata_mutex;json metadata=json::object();
    StationAgent(const fs::path &,std::function<void(const json &)>){}
    bool acquire(){return true;}bool connected(){return true;}void idle(){}void output_active(bool){}
};
struct CallOptions{
    std::string voice,device,microphone,conversation_id,gizmo_id,parent_message_id,voice_session_id;
    std::function<void()> connected;std::function<void(const json &)> update;
};
struct VoiceCall{
    CallOptions options;std::atomic<bool> stopped{false};
    explicit VoiceCall(CallOptions o):options(std::move(o)){}
    json run(const json &){options.connected();while(!stopped)std::this_thread::sleep_for(std::chrono::milliseconds(1));return json::object();}
    void stop(){stopped=true;}void interrupt(){}void check(const std::string & = {}){}bool speaking(){return false;}json stats(){return json::object();}
};
struct ControlServer{ControlServer(int,const fs::path &,std::function<json()>,std::function<json(const json &)>){}void run(){}};
#include "daemon_under_test.hpp"

int main(int argc,char **argv){
    fs::path dir=argv[1];fs::create_directories(dir);
    const std::string id="00000000-0000-4000-8000-000000000001";
    write_json(dir/"settings.json",{{"version",1},{"voice","orbit"},{"selected",id},{"previous",""},
        {"chats",json::array({{{"id",id},{"gizmo_id","g-p-cook"},{"title","Prepared"},{"project_session",true}}})}});
    VoiceDaemon daemon(dir,true);
    auto command=[&]{return json{{"action","prepare_chats"},{"expected_revision",daemon.status().at("settings_revision")},{"add",json::array()},{"remove",json::array()}};};
    assert(daemon.submit(command())["ok"]==true);
    auto rejects=[&]{bool rejected=false;try{daemon.submit(command());}catch(const std::exception &){rejected=true;}assert(rejected);};
    hold_auth=true;daemon.submit({{"action","open"}});
    for(int i=0;i<1000&&!auth_entered;++i)std::this_thread::sleep_for(std::chrono::milliseconds(1));
    assert(auth_entered);rejects();
    daemon.submit({{"action","close"}});
    rejects(); // close must not make an unfinished preparation eligible for mutation.
    hold_auth=false;
    bool prepared=false;
    for(int i=0;i<1000&&!prepared;++i){try{prepared=daemon.submit(command()).value("ok",false);}catch(...){}std::this_thread::sleep_for(std::chrono::milliseconds(1));}
    assert(prepared);daemon.submit({{"action","open"}});
    for(int i=0;i<1000&&daemon.status()["phase"]!="active";++i)std::this_thread::sleep_for(std::chrono::milliseconds(1));
    assert(daemon.status()["phase"]=="active");rejects();
    daemon.submit({{"action","close"}});
    std::cout<<"PASS queued, connecting, cancelled preparation and active call guards\n";
}
