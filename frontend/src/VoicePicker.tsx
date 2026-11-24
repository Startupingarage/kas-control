import {useState} from 'react';
import {Dialog} from '@base-ui/react/dialog';
import {Select} from '@base-ui/react/select';
import {Check,ChevronDown,LoaderCircle,X} from 'lucide-react';
import {call} from './api';

// IDs and aliases match the installed native client's Settings::voice catalog.
const voices=[
  {value:'fathom',label:'Arbor'},{value:'breeze',label:'Breeze'},
  {value:'cove',label:'Cove'},{value:'ember',label:'Ember'},
  {value:'juniper',label:'Juniper'},{value:'maple',label:'Maple'},
  {value:'glimmer',label:'Sol'},{value:'orbit',label:'Spruce'},{value:'vale',label:'Vale'},
];
const voiceName=(id:string)=>voices.find(v=>v.value===id)?.label||id||'Неизвестен';

export function VoicePicker({voice,disabled,onSaved,onBusyChange}:{voice:string;disabled:boolean;onSaved:(voice:string)=>void;onBusyChange:(busy:boolean)=>void}){
  const [open,setOpen]=useState(false),[choice,setChoice]=useState(voice);
  const [saving,setSaving]=useState(false),[error,setError]=useState(''),[saved,setSaved]=useState('');
  async function save(){
    if(saving||!voices.some(v=>v.value===choice)||choice===voice)return;
    setSaving(true);onBusyChange(true);setError('');setSaved('');
    try{
      const result=await call<{voice:string}>('set_voice',choice);
      onSaved(result.voice);setSaved(result.voice);
    }catch(e){setError(e instanceof Error?e.message:String(e));}
    finally{setSaving(false);onBusyChange(false);}
  }
  return <Dialog.Root open={open} onOpenChange={value=>{if(saving)return;setOpen(value);if(value){setChoice(voice);setSaved('');setError('');}}}>
    <Dialog.Trigger className="voice-button" disabled={disabled} aria-label={'Голос: '+voiceName(voice)} title="Выбрать голос для следующего разговора">{voiceName(voice)}<ChevronDown size={13}/></Dialog.Trigger>
    <Dialog.Portal><Dialog.Backdrop className="dialog-backdrop"/><Dialog.Popup className="dialog voice-dialog">
      <div className="dialog-header"><Dialog.Title>Голос Каса</Dialog.Title><Dialog.Close className="icon-button" aria-label="Закрыть выбор голоса" disabled={saving}><X size={18}/></Dialog.Close></div>
      <Dialog.Description className="dialog-description">Новый голос включится при следующем открытии разговора. Текущий сеанс продолжится прежним голосом.</Dialog.Description>
      <label className="form-label" id="voice-label">Голос</label>
      <Select.Root value={choice} disabled={saving} items={voices} onValueChange={value=>{setChoice(value||'');setSaved('');setError('');}}>
        <Select.Trigger className="project-select voice-select" aria-labelledby="voice-label"><Select.Value/><Select.Icon><ChevronDown size={15}/></Select.Icon></Select.Trigger>
        <Select.Portal><Select.Positioner className="project-select-positioner" sideOffset={6} alignItemWithTrigger={false}><Select.Popup className="project-select-popup"><Select.List className="project-options">{voices.map(v=><Select.Item className="project-option" key={v.value} value={v.value}><span className="project-option-check"><Select.ItemIndicator><Check size={14}/></Select.ItemIndicator></span><Select.ItemText>{v.label}</Select.ItemText></Select.Item>)}</Select.List></Select.Popup></Select.Positioner></Select.Portal>
      </Select.Root>
      <p className="field-help">Один выбор для всех проектов. Сохраняется на колонке и работает без ПК.</p>
      {error&&<div className="error-banner" role="alert">{error}</div>}
      {saved&&<p className="voice-saved" role="status">Голос сохранен: {voiceName(saved)}. Включится при следующем открытии.</p>}
      <div className="dialog-actions"><Dialog.Close className="secondary" disabled={saving}>Закрыть</Dialog.Close><button className="primary" disabled={saving||choice===voice||!voices.some(v=>v.value===choice)} onClick={()=>void save()}>{saving&&<LoaderCircle size={15} className="spinning"/>}Сохранить голос</button></div>
    </Dialog.Popup></Dialog.Portal>
  </Dialog.Root>;
}
