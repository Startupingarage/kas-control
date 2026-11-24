#include "settings.hpp"
#include <cassert>
#include <iostream>

template<class T> auto prepare(T &s, const json &add, const json &remove, const std::string &rev, int)
    -> decltype(s.prepare_slots(add, remove, rev)) { return s.prepare_slots(add, remove, rev); }
template<class T> json prepare(T &, const json &, const json &, const std::string &, long) {
    throw std::runtime_error("prepare_slots_unavailable");
}
template<class T> auto revision(T &s, int) -> decltype(T::revision(s.view())) { return T::revision(s.view()); }
template<class T> std::string revision(T &, long) { return "unsupported"; }

json slot(const std::string &id) {
    return {{"id",id},{"gizmo_id","g-p-cook"},{"title","Prepared"},
        {"project_session",true},{"conversation_id",""},{"parent_message_id",""},
        {"voice_session_id",""},{"account_id",""},{"confirmed",false}};
}

int main(int argc,char **argv) {
    fs::path dir=argv[1];fs::create_directories(dir);
    const std::string a="00000000-0000-4000-8000-000000000001";
    const std::string b="00000000-0000-4000-8000-000000000002";
    const std::string c="00000000-0000-4000-8000-000000000003";
    const std::string d="00000000-0000-4000-8000-000000000004";
    auto used=slot(a);used["conversation_id"]="closed-cloud";used["voice_session_id"]="closed-sid";
    json initial={{"version",1},{"voice","orbit"},{"selected",b},{"previous","legacy"},
        {"future_setting",17},{"chats",json::array({used,slot(b),{{"id","legacy"},{"conversation_id","preserve"}}})}};
    write_json(dir/"settings.json",initial);Settings s(dir/"settings.json");
    auto oldrev=revision(s,0);
    try { prepare(s,json::array({slot(c)}),json::array({a}),oldrev,0); }
    catch(const std::exception &e){std::cerr<<"FAIL hot preparation: "<<e.what()<<"\n";return 1;}
    auto expected=initial;expected["chats"]=json::array({slot(b),{{"id","legacy"},{"conversation_id","preserve"}},slot(c)});
    assert(s.view()==expected && read_json(dir/"settings.json")==expected);
    auto reject=[&](const json &add,const json &remove,const std::string &rev){
        auto before=s.view(),disk=read_json(dir/"settings.json");bool threw=false;
        try{prepare(s,add,remove,rev,0);}catch(const std::exception &){threw=true;}
        assert(threw && s.view()==before && read_json(dir/"settings.json")==disk);
    };
    reject(json::array({slot(d)}),json::array(),oldrev);
    for(auto id:{b,std::string("legacy"),std::string("missing"),c})reject(json::array(),json::array({id}),revision(s,0));
    reject(json::array({slot(c)}),json::array(),revision(s,0));
    auto bad=slot(d);bad["conversation_id"]="reuse";reject(json::array({bad}),json::array(),revision(s,0));
    bad=slot(d);bad["voice"]="ember";reject(json::array({bad}),json::array(),revision(s,0));
    bad=slot(d);bad["gizmo_id"]="../wrong";reject(json::array({bad}),json::array(),revision(s,0));
    bad=slot(d);bad["project_session"]=false;reject(json::array({bad}),json::array(),revision(s,0));
    reject(json::array({slot(d),slot(d)}),json::array(),revision(s,0));
    auto edited=expected;edited["future_setting"]=19;write_json(dir/"settings.json",edited);
    reject(json::array({slot(d)}),json::array(),revision(s,0));
    write_json(dir/"settings.json",expected);
    fs::create_directory(dir/"settings.json.new");
    reject(json::array({slot(d)}),json::array(),revision(s,0));
    fs::remove(dir/"settings.json.new");
    prepare(s,json::array({slot(d)}),json::array(),revision(s,0),0);
    Settings reloaded(dir/"settings.json");assert(reloaded.view()==s.view());
    std::cout<<"PASS hot slots, revision, ownership, disk conflict, atomic failure and persistence\n";
}
