import pandas as pd
import requests as req
import hashlib
import base64
import json
import time
import threading as mt
import websocket as wb
import ssl
from queue import Queue, Empty


class GreekAPI:
    def __init__(self, user,s_pwd, pwd,procli,ac_no,is_secure,is_base_64,rest_ip,rest_port):
        self.username = user
        self.session_pwd = s_pwd
        self.userpwd = pwd
        self.gscid = self.username
        self.procli=procli
        self.url_session_token ='http://greekapi.greeksoft.in:3001'
        self.is_secure=is_secure
        self.is_base64 = is_base_64
        self.rest_ip = rest_ip
        self.rest_port = rest_port
        self.session_token = self.get_session_token()
        self.ac_no=ac_no
        self.q = Queue()
        self.o_i=None
        self.tk=None
        self.hb_stop_event =mt.Event()
        if self.is_secure :
            self.ssl_verify = False
            self.wbhd = "https://"
            self.wshd = "wss://"
        else:
            self.ssl_verify = False
            self.wbhd = "http://"
            self.wshd = "ws://"

        self.ws_apollo = None
        self.apollo_port = None
        self.gcid=self.getlogininfo()
        self.jln=self.jlogin_new()
        self.session_id = self.jln[0]
        error_code=self.jln[4]
        self.subscribed_tokens = set()
        self.subscribers = {}


        if error_code == 1:
            print("Password has expired.")
        elif error_code == 2:
            print("Invalid password.")
        elif error_code == 3:
            print("Failure occurred.")
        elif error_code == 4:
            print("Duplicate password not allowed.")
        elif error_code == 5:
            print("Max attempts exceeded for wrong password.")
        elif error_code == 6 or error_code == 7:
            print("Inactive user.")
        elif error_code == 8:
            print("Invalid 2FA answer.")
        elif error_code == 9:
            print("Same ID password.")
        elif error_code == 10:
            print("Same login and transaction passwords.")
        elif error_code == 11:
            print("Guest not registered.")
        elif error_code == 12:
            print("Guest already registered.")
        elif error_code == 13:
            print("Retailer does not exist.")
        elif error_code == 14:
            print("Version mismatch.")
        elif error_code == 17:
            print("Account locked, please contact admin and change password.")
        elif error_code == 18:
            print("Login & transaction password expired.")
        else:
            print('Connection Has Been Established Successfully!')
            session_id = self.jln[0]
        gcid=self.gcid
        print(self.session_id)
        if self.session_id == None:
            print('Please Check or Change the password and Try Again !!!')
            self.close_connection()

    def add_tokens(self, tokens):
        new_tokens = []
        for t in tokens:
            if str(t) not in self.subscribed_tokens:
                self.subscribed_tokens.add(str(t))
                new_tokens.append(t)

        if new_tokens:
            self.subscribe_token(new_tokens)


    def base64_to_json(self, coded_string):
        string = base64.b64decode(coded_string).decode('utf-8')
        return json.loads(string)

    def register_subscriber(self, name, tokens):
        self.subscribers[name] = {
            "tokens": set(tokens),
            "queue": Queue()
        }
        self.add_tokens(tokens)


    def get_session_token(self):
        url = f'{self.url_session_token}/auth/greek/sessiontoken'
        myobj = {
            "username": str(self.username),
            "password": str(self.session_pwd),
            "validFor": str("1d")
        }

        response = req.post(url, json=myobj)
        response.raise_for_status()  # Raise an error for bad responses

        # Get the session token from the response
        session_token = response.json().get('sessionToken')

        return session_token

    def json_to_base64(self, string):
        string = json.dumps(string)
        coded_string = base64.b64encode(string.encode('utf-8'))
        coded_string = repr(coded_string)[2:-1]
        return coded_string

    def get_url(self,servicename):
        svcname = servicename
        url = self.wbhd+self.rest_ip+ ":"+self.rest_port+"/"+svcname
        return url
    def token_broadcast(self,tokenno, assettype):
        gcid=self.gcid
        params = {
            "request": {
                "data": {
                    "token": tokenno,
                    "assetType": assettype,
                    "gscid": str(self.username),
                    "gcid": str(gcid),
                },
                "svcName": "getQuoteForSingleSymbol_V2",
                "svcGroup": "Markets"
            }
        }
        if self.is_base64:
            params = self.json_to_base64(params)
            stoken = self.session_token
            headers = { "Authorization":""+str(stoken) ,"charset": "utf-8", "Content-Type": "application/json" }
            svcname = "getQuoteForSingleSymbol_V2"
            url_neworder = self.get_url(svcname)
            y1 =  req.post(url_neworder,data=params,headers=headers ,verify=self.ssl_verify )
            y1 = y1.text
            z1 = self.base64_to_json(y1)
            df_broadcast_response = z1.get('response')
            return df_broadcast_response.get('data')
        else:
            stoken = self.session_token
            headers = { "Authorization":""+str(stoken) ,"charset": "utf-8", "Content-Type": "application/json" }
            svcname = "getQuoteForSingleSymbol_V2"
            url_neworder = self.get_url(svcname)
            y1 =  req.post(url_neworder,json=params,headers=headers ,verify=self.ssl_verify )
            z1 = y1.json()
            df_broadcast_response = z1.get('response')
            return df_broadcast_response.get('data')

    def MBP_data(self):
        params={
            "request": {
                "data": {
                    "mode": "FULL",
                    "exchangeTokens": [
                        {
                            "exchange": "NSE",
                            "token": "101000025"
                        },
                        {
                            "exchange": "NSE",
                            "token": "101000324"
                        },
                        {
                            "exchange": "NSE",
                            "token": "101007936"
                        },
                        {
                            "exchange": "NSE",
                            "token": "101000547"
                        },
                        {
                            "exchange": "NSE",
                            "token": "101021174"
                        },
                        {
                            "exchange": "NSE",
                            "token": "101021508"
                        },
                        {
                            "exchange": "NSE",
                            "token": "101000827"
                        },
                        {
                            "exchange": "NSE",
                            "token": "101019913"
                        },
                        {
                            "exchange": "NSE",
                            "token": "101000910"
                        },
                        {
                            "exchange": "NSE",
                            "token": "101004717"
                        }
                    ]
                },
                "svcName": "getToken_OnlyMbpData",
                "svcGroup": "Markets"
            }
        }
        if self.is_base64:
            params = self.json_to_base64(params)
            stoken = self.session_token
            headers = { "Authorization":""+str(stoken) ,"charset": "utf-8", "Content-Type": "application/json" }
            svcname = "getToken_OnlyMbpData"
            url_neworder = self.get_url(svcname)
            y1 =  req.post(url_neworder,data=params,headers=headers ,verify=self.ssl_verify )
            y1 = y1.text
            z1 = self.base64_to_json(y1)
            df_broadcast_response = z1.get('response')
            return df_broadcast_response.get('data')
        else:
            stoken = self.session_token
            headers = { "Authorization":""+str(stoken) ,"charset": "utf-8", "Content-Type": "application/json" }
            svcname = "getToken_OnlyMbpData"
            url_neworder = self.get_url(svcname)
            y1 =  req.post(url_neworder,json=params,headers=headers ,verify=self.ssl_verify )
            z1 = y1.json()
            df_broadcast_response = z1.get('response')
            return df_broadcast_response.get('data')

    def server_time(self):
        gcid=self.gcid
        params = {
            "request": {
                "data": {
                    "token": '101999957',
                    "assetType": 'INDEX',
                    "gscid": str(self.username),
                    "gcid": str(gcid),
                },
                "svcName": "getQuoteForSingleSymbol_V2",
                "svcGroup": "Markets"
            }
        }
        if self.is_base64:
            params = self.json_to_base64(params)
            stoken = self.session_token
            headers = { "Authorization":""+str(stoken) ,"charset": "utf-8", "Content-Type": "application/json" }
            svcname = "getQuoteForSingleSymbol_V2"
            url_neworder = self.get_url(svcname)
            y1 =  req.post(url_neworder,data=params,headers=headers ,verify=self.ssl_verify )
            y1 = y1.text
            z1 = self.base64_to_json(y1)
            server_time_resp = z1.get('response',{}).get('serverTime')
            server_time_resp_conv=pd.to_datetime(server_time_resp, unit='s', errors='coerce').tz_localize('UTC').tz_convert("Asia/Kolkata").strftime('%d-%m-%Y %H:%M:%S')
            return server_time_resp_conv
        else:
            stoken = self.session_token
            headers = { "Authorization":""+str(stoken) ,"charset": "utf-8", "Content-Type": "application/json" }
            svcname = "getQuoteForSingleSymbol_V2"
            url_neworder = self.get_url(svcname)
            y1 =  req.post(url_neworder,json=params,headers=headers ,verify=self.ssl_verify )
            z1 = y1.json()
            server_time_resp = z1.get('response',{}).get('serverTime')
            server_time_resp_conv=pd.to_datetime(server_time_resp, unit='s', errors='coerce').tz_localize('UTC').tz_convert("Asia/Kolkata").strftime('%d-%m-%Y %H:%M:%S')
            return server_time_resp_conv

    def getlogininfo(self):
        svcname = "getLoginInfo"
        # time.sleep(10)
        url_getlogininfo = self.get_url(svcname)
        stoken = self.session_token
        headers = { "Authorization":""+str(stoken) ,"charset": "utf-8", "Content-Type": "application/json" }
        svc_req = {
            "request": {
                "svcVersion": "1.0.0",
                "svcGroup": "Login",
                "svcName": "getlogininfo",
                "assetType": "",
                "data": {
                    "gscid": str(self.username)
                }
            }
        }
        if self.is_base64 :
            svc_req = self.json_to_base64(svc_req)
            svc_res =  req.request("POST",url=url_getlogininfo,data=svc_req,headers=headers,verify=False )
            svc_res = svc_res.text
            svc_res = self.base64_to_json(svc_res)
        else:
            svc_res =  req.request("POST",url=url_getlogininfo,json=svc_req,headers=headers,verify=False )
            svc_res=svc_res.json()
        gcid = svc_res['response']['data']['gcid']
        return gcid

    def jlogin_new(self):
        url_jloginnew = f"http://{self.rest_ip}:{self.rest_port}/jloginNew"
        pwdhash = hashlib.md5(self.userpwd.encode()).hexdigest()
        svc_req = {
            "request": {
                "data": {
                    "gscid": str(self.username),
                    "deviceDetails": "",
                    "deviceType": "0",
                    "pass": str(pwdhash),
                    "transPass": "",
                    "userType": "Customer",
                    "brokerid": "1",
                    "passType": "0",
                    "version_no": "1.0.1.10",
                    "encryptionType": "1"
                },
                "svcName": "jloginNew",
                "svcGroup": "Login"
            }
        }

        if self.is_base64:
            svc_req = self.json_to_base64(svc_req)
            headers = {"Authorization": self.session_token, "charset": "utf-8", "Content-Type": "application/json"}
            svc_res = req.post(url_jloginnew, data=svc_req, headers=headers, verify=False)
            svc_res = self.base64_to_json(svc_res.text)
        else:
            headers = {"Authorization": self.session_token, "charset": "utf-8", "Content-Type": "application/json"}
            svc_res = req.post(url_jloginnew, json=svc_req, headers=headers, verify=False)
            svc_res=svc_res.json()
        self.apollo_port=svc_res['response']['data'].get('Apollo_Port')
        self.iris_port=svc_res['response']['data'].get('Iris_Port')
        session_id = svc_res['response']['sessionId']
        error_code=svc_res['response']['ErrorCode']
        self.session_id=session_id
        websocket_broadcast_ip = svc_res['response']['data']['Apollo_IP']
        websocket_broadcast_port = svc_res['response']['data']['Apollo_Port']
        websocket_order_ip = svc_res['response']['data']['Iris_IP']
        return session_id, websocket_broadcast_ip, websocket_broadcast_port, websocket_order_ip,error_code

    def Net_Position_Details_strategywise(self,type):
        svcname = "getStrategyNameWiseNetPositionDetail?"
        url_netposition_sw = self.get_url(svcname)
        if type=='EXP':
            text = "gscid={}&type=expiry".format(str(self.username))
        else:
            text = "gscid={}".format(str(self.username))
        if self.is_base64:
            coded_string = base64.b64encode(text.encode('utf-8'))
            coded_string = repr(coded_string)[2:-1]
            info = url_netposition_sw + coded_string
            stoken = self.session_token
            headers = { "Authorization":""+str(stoken) ,"charset": "utf-8", "Content-Type": "application/json" }
            y1 =  req.request("GET", info, headers=headers ,verify=self.ssl_verify )
            y1 = y1.text
            z1 = self.base64_to_json(y1)
            response = z1.get('data')
        else:
            info = url_netposition_sw + text
            stoken = self.session_token
            headers = { "Authorization":""+str(stoken) ,"charset": "utf-8", "Content-Type": "application/json" }
            y1 =  req.request("GET", info, headers=headers ,verify=self.ssl_verify )
            z1 = y1.json()
            response = z1.get('data')

        return response

    def Net_Position_request(self):
        svcname="NPRequest"
        url_net_position=self.get_url(svcname)
        np_param={
            "request": {
                "FormFactor": "M",
                "data": {
                    "gscid": str(self.username)
                },
                "svcGroup": "portfolio",
                "svcVersion": "1.0.0",
                "streaming_type": "NPRequest",
                "request_type": "subscribe"
            }
        }
        if self.is_base64:
            np_param=self.json_to_base64(np_param)
            stoken = self.session_token
            headers = { "Authorization":""+str(stoken) ,"charset": "utf-8", "Content-Type": "application/json" }
            y1 = req.post(url_net_position,data=np_param,headers=headers,verify=self.ssl_verify)
            y1 = y1.text
            z1 = self.base64_to_json(y1)
            # np_resp=pd.json_normalize(z1['response'])
        else:
            stoken = self.session_token
            headers = { "Authorization":""+str(stoken) ,"charset": "utf-8", "Content-Type": "application/json" }
            y1 = req.post(url_net_position,json=np_param,headers=headers,verify=self.ssl_verify)
            z1 = y1.json()
        np_resp=z1.get('response', {}).get('stockDetails')
        return np_resp

    def Net_position_Detailed(self):
        svcname="NPDetailRequest"
        url_net_pos_detailed=self.get_url(svcname)
        np_d_param={
            "request": {
                "FormFactor": "M",
                "data": {
                    "gscid": str(self.username)
                },
                "svcGroup": "portfolio",
                "svcVersion": "1.0.0",
                "streaming_type": "NPDetailRequest",
                "request_type": "subscribe"
            }
        }
        if self.is_base64:
            np_d_param=self.json_to_base64(np_d_param)
            stoken = self.session_token
            headers = { "Authorization":""+str(stoken) ,"charset": "utf-8", "Content-Type": "application/json" }
            y1 = req.post(url_net_pos_detailed,data=np_d_param,headers=headers,verify=self.ssl_verify)
            y1 = y1.text
            z1 = self.base64_to_json(y1)
            np_detailed_resp=z1.get('response')
        else:
            stoken = self.session_token
            headers = { "Authorization":""+str(stoken) ,"charset": "utf-8", "Content-Type": "application/json" }
            y1 = req.post(url_net_pos_detailed,json=np_d_param,headers=headers,verify=self.ssl_verify)
            z1 = y1.json()
            np_detailed_resp=z1.get('response')
        return np_detailed_resp.get('stockDetails')

    def Orderbook_All(self):
        svc_name="getOrderBookDetailWithLegV2?"
        url_ordbook_all=self.get_url(svc_name)

        stoken=self.session_token
        orderbook_all="exchangeType=ALL&ClientCode={}&Order_Status=ALL&Ordertype=ALL&gscid={}".format(self.gcid,self.username)
        if self.is_base64:
            orderbook_all_str=base64.b64encode(orderbook_all.encode('utf-8'))
            orderbook_all_str=repr(orderbook_all_str)[2:-1]
            orderbook_all_info=url_ordbook_all+orderbook_all_str
            headers={ "Authorization":""+str(stoken) ,"charset": "utf-8", "Content-Type": "application/json" }
            all_ord_stat=req.request("GET", orderbook_all_info, headers=headers, verify=self.ssl_verify)
            all_ord_stat=self.base64_to_json(all_ord_stat.text)
        else:
            orderbook_all_info=url_ordbook_all+orderbook_all
            headers={ "Authorization":""+str(stoken) ,"charset": "utf-8", "Content-Type": "application/json" }
            all_ord_stat=req.request("GET", orderbook_all_info, headers=headers, verify=self.ssl_verify)
            all_ord_stat=all_ord_stat.json()
        return all_ord_stat.get('data')

    def Orderbook_lite(self,order_id):
        svc_name="getOrderBookDetail_Lite?"
        url_ordbook_lite=self.get_url(svc_name)

        stoken=self.session_token
        orderbook_lite="greekOrderNo={}&gscid={}".format(order_id,self.username)
        if self.is_base64:
            orderbook_lite_str=base64.b64encode(orderbook_lite.encode('utf-8'))
            orderbook_lite_str=repr(orderbook_lite_str)[2:-1]
            orderbook_lite_info=url_ordbook_lite+orderbook_lite_str
            headers={ "Authorization":""+str(stoken) ,"charset": "utf-8", "Content-Type": "application/json" }
            lite_ord_stat=req.request("GET", orderbook_lite_info, headers=headers, verify=self.ssl_verify)
            lite_ord_stat=self.base64_to_json(lite_ord_stat.text)
        else:
            orderbook_lite_info=url_ordbook_lite+orderbook_lite
            headers={ "Authorization":""+str(stoken) ,"charset": "utf-8", "Content-Type": "application/json" }
            lite_ord_stat=req.request("GET", orderbook_lite_info, headers=headers, verify=self.ssl_verify)
            lite_ord_stat=lite_ord_stat.json()

        return lite_ord_stat.get('data')

    def Orderbook_Traded(self):
        svc_name="getOrderBookDetailWithLegV2?"
        url_ordbook_trded=self.get_url(svc_name)

        stoken=self.session_token
        orderbook_trd="exchangeType=ALL&ClientCode={}&Order_Status=ALL&Ordertype=ALL&gscid={}".format(self.gcid,self.username)
        if self.is_base64:
            orderbook_trd_str=base64.b64encode(orderbook_trd.encode('utf-8'))
            orderbook_trd_str=repr(orderbook_trd_str)[2:-1]
            orderbook_trded_info=url_ordbook_trded+orderbook_trd_str
            headers={ "Authorization":""+str(stoken) ,"charset": "utf-8", "Content-Type": "application/json" }
            trded_ord_stat=req.request("GET", orderbook_trded_info, headers=headers, verify=self.ssl_verify)
            trded_ord_stat=self.base64_to_json(trded_ord_stat.text)
        else:
            orderbook_trded_info=url_ordbook_trded+orderbook_trd
            headers={ "Authorization":""+str(stoken) ,"charset": "utf-8", "Content-Type": "application/json" }
            trded_ord_stat=req.request("GET", orderbook_trded_info, headers=headers, verify=self.ssl_verify)
            trded_ord_stat=trded_ord_stat.json()
        return trded_ord_stat.get('data')

    def Orderbook_Rejected (self):
        svc_name="getOrderBookDetailWithLegV2?"
        url_rejected_ord=self.get_url(svc_name)

        stoken=self.session_token
        rejected_text="exchangeType=ALL&ClientCode={}&Order_Status=RMS_REJECTED&Ordertype=All&gscid={}".format(self.gcid,self.username)
        if self.is_base64:
            rejected_encoded_str=base64.b64encode(rejected_text.encode('utf-8'))
            rejected_encoded_str=repr(rejected_encoded_str)[2:-1]
            rejected_info=url_rejected_ord+rejected_encoded_str
            headers={ "Authorization":""+str(stoken) ,"charset": "utf-8", "Content-Type": "application/json" }
            rej_stat=req.request("GET", rejected_info, headers=headers, verify=self.ssl_verify)
            rej_stat=self.base64_to_json(rej_stat.text)
        else:
            rejected_info=url_rejected_ord+rejected_text
            headers={ "Authorization":""+str(stoken) ,"charset": "utf-8", "Content-Type": "application/json" }
            rej_stat=req.request("GET", rejected_info, headers=headers, verify=self.ssl_verify)
            rej_stat=rej_stat.json()
        return rej_stat.get('data')

    def send_apollo_resp(self):
        ws_apollo = wb.create_connection(f"{self.wshd}{self.rest_ip}:{self.apollo_port}", sslopt={"cert_reqs": ssl.CERT_NONE})
        self.ws_apollo=ws_apollo
        apollo_login_req = {"request": {"data": {"gscid": str(self.username),"gcid": str(self.gcid),"sessionId": str(self.session_id),"device_type": "0"},"response_format": "json","request_type": "subscribe","streaming_type": "login"}}
        if self.is_base64:
            apollo_login_req = self.json_to_base64(apollo_login_req)
            ws_apollo.send( apollo_login_req )
            apollo_login_res = ws_apollo.recv()
            apollo_login_res = self.base64_to_json(apollo_login_res)
        else:
            ws_apollo.send(json.dumps(apollo_login_req))
            apollo_login_res=ws_apollo.recv()
        print("Apollo Login Response:", apollo_login_res)
        return apollo_login_res

    def on_open(self, wb):
        print("Connection opened")
        apollo_login_req = {
            "request": {
                "data": {
                    "gscid": str(self.username),
                    "gcid": str(self.gcid),
                    "sessionId": str(self.session_id),
                    "device_type": "0"
                },
                "response_format": "json",
                "request_type": "subscribe",
                "streaming_type": "login"
            }
        }
        if self.is_base64:
            apollo_login_req = self.json_to_base64(apollo_login_req)
            wb.send(apollo_login_req)
        else:
            wb.send(json.dumps(apollo_login_req))
        self.subscribe_token(self.token_list)
        # Start heartbeat thread after login
        mt.Thread(target=self.heartbeat_req, args=(wb,), daemon=True).start()

    # def update_password(self,password,brokerid):
    def update_password(self,password,brokerid):
        # pass_rst_url=f"http://{self.rest_ip}:{self.rest_port}/user/gsm/updatePassword"
        # pass_rst_url='http://greekapi.greeksoft.in:3010/user/gsm/updatePassword'
        pass_rst_url='http://192.168.207.18:3434/SetPassword'

        Body={"request":{"data":{"gscid":str(self.username), "NewPass":str(password), "ConfirmPass":str(password), "IsIBTSecPass":0, "UnlockOrPasswordChange":1, "IsIBTUser":1}}}
        headers={"Authorization": self.session_token, "charset": "utf-8", "Content-Type": "application/json"}
        response = req.post(pass_rst_url,data=Body,headers=headers ,verify=self.ssl_verify )
        response.raise_for_status()  # Raise an error for bad responses
        return response.json()


    def on_message(self, wb, message):
        if self.is_base64:
            apollo_res=self.base64_to_json(message)
        else:
            apollo_res = json.loads(message)
        resp = apollo_res.get('response', {})
        service_name = resp.get('svcName')
        streaming = resp.get('streaming_type')
        # print(resp)
        if service_name == 'OpenInterest' and streaming == 'OpenInterest':
            self.tk=resp.get('data',{}).get('gtoken')
            self.o_i=resp.get('data',{}).get('currentOI')
        if service_name == 'Broadcast' and streaming == 'marketPicture':
            tkn=resp.get('data', {}).get('symbol')
            if tkn in self.token_counter:
                if self.token_counter[tkn] < 3:
                    self.token_counter[tkn] += 1
                    return
            sym=resp.get('data', {}).get('name')
            ltp=resp.get('data', {}).get('ltp')
            ltt=resp.get('data',{}).get('ltt')
            bid=resp.get('data',{}).get('bid')
            ask=resp.get('data',{}).get('ask')
            tot_vol=resp.get('data',{}).get('tot_vol')
            depth=resp.get('data',{}).get('level2')
            app_res=resp.get('data')
            oi = None
            if tkn == self.tk:
                oi = self.o_i
            if self.req_data=='depth':
                packed_data=tkn,sym,ltp,depth,ltt,oi
                self.q.put(packed_data)
                for sub in self.subscribers.values():
                    if tkn in sub["tokens"]:
                        sub["queue"].put(packed_data)

            elif self.req_data=='ask/bid':
                    packed_data=tkn,sym,bid,ask,ltt,oi
                    self.q.put(packed_data)
                    for sub in self.subscribers.values():
                        if tkn in sub["tokens"]:
                            sub["queue"].put(packed_data)

            elif self.req_data=='allresp':
                    packed_data=app_res
                    print("WS MESSAGE RECEIVED")
                    print("Pushing:", packed_data)
                    self.q.put(packed_data)
                    for sub in self.subscribers.values():
                        if tkn in sub["tokens"]:
                            sub["queue"].put(packed_data)

            else:
                packed_data=tkn,sym,ltp,ltt,tot_vol,oi
                self.q.put(packed_data)
                for sub in self.subscribers.values():
                    if tkn in sub["tokens"]:
                        sub["queue"].put(packed_data)


    def data_stream(self):
        while True:
            message = self.q.get()   # BLOCKING
            yield message


    def on_error(self, wb, error):
        print("Error:", error)

    def on_close(self, ws, close_status_code, close_msg):
        print("Connection closed!",close_status_code,close_msg)

    def start_apollo(self, token_list,req_data):
        self.token_list = token_list
        self.req_data=req_data
        self.token_counter = {str(t): 0 for t in token_list}
        url = f"{self.wshd}{self.rest_ip}:{self.apollo_port}"
        self.ws_apollo = wb.WebSocketApp(url,
                                         on_open=self.on_open,
                                         on_message=self.on_message,
                                         on_error=self.on_error,
                                         on_close=self.on_close)

        thread = mt.Thread(target=self.ws_apollo.run_forever, kwargs={"sslopt": {"cert_reqs": 0}})
        thread.daemon = True
        thread.start()

    # def start_iris(self):
    #     ir

    def heartbeat_req(self,ws_apollo):
        gcid=self.gcid
        apollo_hb_req = {"request": {"data": {"gcid": str(gcid),"sessionId": str(self.session_id) },"response_format": "json","request_type": "subscribe","streaming_type": "HeartBeat"}}
        if self.is_base64:
            apollo_hb_req = self.json_to_base64(apollo_hb_req)
        else:
            apollo_hb_req = apollo_hb_req

        while self.hb_stop_event is None:
            ws_apollo.send(apollo_hb_req)
            time.sleep(30)
            # print('Apollo Heart Beat request send!!!')

    def subscribe_token(self, token):
        gcid=self.gcid
        username=self.username
        if gcid and username:
            for tkn in token:
                s1_nifty = {"symbol": str(tkn)}
                apollo_subscribe_req_n = {
                    "request": {
                        "data": {
                            "symbols": [s1_nifty]
                        },
                        "response_format": "json",
                        "gscid": str(self.username),
                        "gcid": gcid,  # Assuming gcid is the same as gscid for this example
                        "request_type": "subscribe",
                        "streaming_type": "marketPicture"
                    }
                }
                if self.is_base64:
                    apollo_subscribe_req_n = self.json_to_base64(apollo_subscribe_req_n)
                    self.ws_apollo.send(apollo_subscribe_req_n)
                else:
                    self.ws_apollo.send(json.dumps(apollo_subscribe_req_n))
            print(f"Subscribed to token: {token}")
            return f"Subscribed to token: {token}"
        else:
            print("Cannot subscribe. Session ID or GCID is not set.")

    def unsubscribe_token(self,token):
        gcid=self.gcid
        username=self.username
        if gcid and username:
            s1_nifty = {"symbol": str(token)}
            apollo_unsubscribe_req_n={
                "request": {
                    "data": {
                        "symbols": [
                            {
                                "symbol": [s1_nifty]
                            }
                        ]
                    },
                    "response_format": "json",
                    "gscid": str(self.username),
                    "gcid": gcid,
                    "request_type": "unsubscribe",
                    "streaming_type": "marketPicture"
                }
            }
            if self.is_base64:
                apollo_unsubscribe_req_n = self.json_to_base64(apollo_unsubscribe_req_n)
                self.ws_apollo.send(apollo_unsubscribe_req_n)
            else:
                self.ws_apollo.send(json.dumps(apollo_unsubscribe_req_n))
            return print(f"UnSubscribed to token: {token}")

    def get_margin_details(self):
        params={
            "request": {
                "FormFactor": "M",
                "data": {
                    "gcid": str(self.gcid),
                    "sessionId": str(self.session_id),
                    "segment": 2,
                    "exchange_type": "-1"
                },
                "svcGroup": "portfolio",
                "svcVersion": "1.0.0",
                "streaming_type": "MarginDetailRequest",
                "request_type": "subscribe"
            }
        }
        if self.is_base64:
            params=self.json_to_base64(params)
            stoken = self.session_token
            headers = { "Authorization":""+str(stoken) ,"charset": "utf-8", "Content-Type": "application/json" }
            svcname = "MarginDetailRequest"
            url_neworder = self.get_url(svcname)
            y1 =  req.post(url_neworder,data=params,headers=headers ,verify=self.ssl_verify )
            y1 = y1.text
            z1 = self.base64_to_json(y1)
        else:
            stoken = self.session_token
            headers = { "Authorization":""+str(stoken) ,"charset": "utf-8", "Content-Type": "application/json" }
            svcname = "NewOrderRequest"
            url_neworder = self.get_url(svcname)
            y1 =  req.post(url_neworder,json=params,headers=headers ,verify=self.ssl_verify )
            z1 = y1.json()
        df_order_response = z1.get('response',{}).get('data')
        data_list = [df_order_response]
        return data_list

    def get_holding_details(self):
        params={
            "FormFactor": "M",
            "data": {
                "gscid": str(self.username),
                "gcid": str(self.gcid),
                "sessionId": str(self.session_id),
            },
            "svcVersion": "portfolio",
            "svcGroup": "1.0.0",
            "streaming_type": "HoldingDetailsInfo",
            "request_type": "subscribe"
        }
        if self.is_base64 :
            params = self.json_to_base64(params)
            stoken = self.session_token
            headers = { "Authorization":""+str(stoken) ,"charset": "utf-8", "Content-Type": "application/json" }
            svcname = "HoldingDetailsInfo"
            url_neworder = self.get_url(svcname)
            y1 =  req.post(url_neworder,data=params,headers=headers ,verify=self.ssl_verify )
            y1 = y1.text
            z1 = self.base64_to_json(y1)
        else:
            stoken = self.session_token
            headers = { "Authorization":""+str(stoken) ,"charset": "utf-8", "Content-Type": "application/json" }
            svcname = "HoldingDetailsInfo"
            url_neworder = self.get_url(svcname)
            y1 =  req.post(url_neworder,json=params,headers=headers ,verify=self.ssl_verify )
            z1 = y1.json()
        df_order_response = z1.get('response',{}).get('data',{}).get('stockDetails')
        return df_order_response


    def place_order(self,tokenno,symbol,lot,qty,price,buysell,ordtype,trigprice,exchange,validity,strategyname):
        if self.session_id==None:
            print('Order cant be Placed!')
            return
        params = {
            "request": {
                "data": {
                    "trigger_price": str(trigprice),
                    "gtoken": str(tokenno),
                    "side": str(buysell),
                    "gcid": str(self.gcid),
                    "validity": str(validity),
                    "price": str(price),
                    "exchange": str(exchange),
                    "disclosed_qty": "0",
                    "tradeSymbol": str(symbol),
                    "lot": str(lot),
                    "iprocli":str(self.procli),
                    "order_type": str(ordtype),
                    "product": "0",
                    "qty": str(qty),
                    "corderid": "3",
                    "amo": "0",
                    "AccountNumber":str(self.ac_no),
                    "is_restapi":"1",
                    "gtdExpiry": 0,
                    "is_post_closed": "0",
                    "is_preopen_order": "0",
                    "isSqOffOrder": "false",
                    "offline": "0",
                    "strategyName":str(strategyname),
                    "strategyNo":"124"
                },
                "response_format": "json",
                "request_type": "subscribe",
                "streaming_type": "NewOrderRequest"
            }
        }

        if self.procli != "1":
            # Remove 'AccountNumber' from the data dictionary if procli is not "1"
            del params["request"]["data"]["AccountNumber"]
        if self.is_base64 :
            params = self.json_to_base64(params)
            stoken = self.session_token
            headers = { "Authorization":""+str(stoken) ,"charset": "utf-8", "Content-Type": "application/json" }
            svcname = "NewOrderRequest"
            url_neworder = self.get_url(svcname)
            y1 =  req.post(url_neworder,data=params,headers=headers ,verify=self.ssl_verify )
            y1 = y1.text
            z1 = self.base64_to_json(y1)
        else:
            stoken = self.session_token
            headers = { "Authorization":""+str(stoken) ,"charset": "utf-8", "Content-Type": "application/json" }
            svcname = "NewOrderRequest"
            url_neworder = self.get_url(svcname)
            y1 =  req.post(url_neworder,json=params,headers=headers ,verify=self.ssl_verify )
            z1 = y1.json()
        df_order_response = z1.get('response')
        return df_order_response

    def Order_Trade_status(self,ord_id):
        svcname = "getOrderDetail?"
        url_trade_stat = self.get_url(svcname)
        # time.sleep(2)
        stoken = self.session_token
        text = "greekOrderNo={}&gscid={}".format(ord_id, str(self.username))
        if self.is_base64:
            coded_string = base64.b64encode(text.encode('utf-8'))
            coded_string = repr(coded_string)[2:-1]
            info = url_trade_stat + coded_string
            headers = { "Authorization":""+str(stoken) ,"charset": "utf-8", "Content-Type": "application/json" }
            ord_status = req.request("GET", info, headers=headers, verify=False)
            response = self.base64_to_json(ord_status.text)

        else:
            info = url_trade_stat + text
            headers = { "Authorization":""+str(stoken) ,"charset": "utf-8", "Content-Type": "application/json" }
            ord_status = req.request("GET", info, headers=headers, verify=False)
            # print(ord_status)
            response = ord_status.json()
        return response.get('data', [{}])[0]


    def all_pending_order(self):
        svc_name="getOrderBookDetailWithLegV2?"
        url_pending_order=self.get_url(svc_name)

        stoken=self.session_token
        pending_text="exchangeType=ALL&ClientCode={}&Order_Status=Pending&Ordertype=All&gscid={}".format(self.gcid,self.username)
        if self.is_base64:
            pending_encoded_string= base64.b64encode(pending_text.encode('utf-8'))
            pending_encoded_string=repr(pending_encoded_string)[2:-1]
            pending_info=url_pending_order+pending_encoded_string
            headers = { "Authorization":""+str(stoken) ,"charset": "utf-8", "Content-Type": "application/json" }
            pen_stat=req.request("GET", pending_info, headers=headers, verify=self.ssl_verify)
            pen_response=self.base64_to_json(pen_stat.text)
        else:
            pending_info=url_pending_order+pending_text
            headers = { "Authorization":""+str(stoken) ,"charset": "utf-8", "Content-Type": "application/json" }
            pen_stat=req.request("GET", pending_info, headers=headers, verify=self.ssl_verify)
            pen_response=pen_stat.json()
        return pen_response

    def cancel_order(self,ord_id):
        stoken = self.session_token
        svcname = 'Order/'
        url_cancel_ord = self.get_url(svcname)
        url_cancel_ord = url_cancel_ord + str(ord_id)

        headers = { "Authorization":""+str(stoken) ,"charset": "utf-8", "Content-Type": "application/json" }
        params = ""
        if self.is_base64:
            can_response = req.request("DELETE", url_cancel_ord, data=params, headers=headers, verify=self.ssl_verify)
            can_response=self.base64_to_json(can_response.text)
            message=can_response.json().get('success')
            if message=='true':
                print(f'Order_No: {ord_id}, has been Cancelled!')
            else:
                print(f'Error While Cancelling Order_No: {ord_id} !')
        else:
            can_response = req.request("DELETE", url_cancel_ord, json=params, headers=headers, verify=self.ssl_verify)
            message=can_response.json().get('success')
            if message=='true':
                print(f'Order_No: {ord_id}, has been Cancelled!')
            else:
                print(f'Error While Cancelling Order_No: {ord_id} !')


    def modify_order(self,price,lot,qty,ordtype,gorderid):
        svcname = 'SmallModifyOrderRequest'
        url_neworder = self.get_url(svcname)
        #ord_status = req.get(f"{url_neworder}/{ord_id}")
        params = {
            "request": {
                "data": {
                    "trigger_price": "0",
                    "gcid": str(self.gcid),
                    "validity": "0",
                    "price": str(price),
                    "gorderid": str(gorderid),
                    "order_type": str(ordtype),
                    "lot":str(lot),
                    "qty": str(qty),
                    "disclosed_qty": "0",
                    "amo": "0",
                    "sl_price":"0",
                    "gtdExpiry": "0"
                },
                "response_format": "json",
                "request_type": "subscribe",
                "streaming_type": "ModifyOrderRequest"
            }
        }

        if self.is_base64:
            params = self.json_to_base64(params)
            stoken = self.session_token
            headers = { "Authorization":""+str(stoken) ,"charset": "utf-8", "Content-Type": "application/json" }
            y1 =  req.post(url_neworder,data=params,headers=headers ,verify=self.ssl_verify )
            y1 = y1.text
            z1 = self.base64_to_json(y1)
            response = z1.get('response')
        else:
            stoken = self.session_token
            headers = { "Authorization":""+str(stoken) ,"charset": "utf-8", "Content-Type": "application/json" }
            y1 =  req.post(url_neworder,json=params,headers=headers ,verify=self.ssl_verify )
            z1 = y1.json()
            response = z1.get('response')
        return response

    # def get_ohlc_data(self,token,date,interval):
    #     service_name='get_ohlc'
    #     url_ohlc_data=self.get_url(service_name)
    #     params={
    #         "request": {
    #             "FormFactor": "M",
    #             "data": {
    #                 "gscid": str(self.username),
    #                 "token": int(token),
    #                 "interval": int(interval),
    #                 "date": str(date), #20231218
    #                 # "time": 0,
    #                 "noofdays": 1
    #             },
    #             "svcGroup": "portfolio",
    #             "svcVersion": "1.0.0",
    #             "svcName": "jhistorical_New",
    #             "requestType": "U"
    #         }
    #     }
    #     if self.is_base64:
    #         params = self.json_to_base64(params)
    #         stoken = self.session_token
    #         headers = { "Authorization":""+str(stoken) ,"charset": "utf-8", "Content-Type": "application/json" }
    #         y1 =  req.post(url_ohlc_data,data=params,headers=headers ,verify=self.ssl_verify )
    #         y1 = y1.text
    #         z1 = self.base64_to_json(y1)
    #         response = z1.get('response',{}).get('data',{}).get('data')
    #     else:
    #         stoken = self.session_token
    #         headers = { "Authorization":""+str(stoken) ,"charset": "utf-8", "Content-Type": "application/json" }
    #         y1 =  req.post(url_ohlc_data,json=params,headers=headers ,verify=self.ssl_verify )
    #         z1 = y1.json()
    #         response = z1.get('response',{}).get('data',{}).get('data')
    #
    #     return response
    def get_ohlc_data(self, token, date, interval, max_retries=5, timeout=60):
        """
        Robust fetch for OHLC data with retries.

        Parameters:
          - token (int | str): token id
          - date (str): date in format expected by API, e.g. '20231218'
          - interval (int): interval in minutes or API-specific unit
          - max_retries (int): maximum attempts (default 5)
          - timeout (int | float): request timeout in seconds


        Returns:
          - response data (list/dict) or raises GreekAPIError when exhausted and raise_on_empty True.
        """
        svcname = 'get_ohlc'
        url_ohlc_data = self.get_url(svcname)

        # Build the request payload (keeps the original structure you used)
        params = {
            "request": {
                "FormFactor": "M",
                "data": {
                    "gscid": str(self.username),
                    "token": int(token),
                    "interval": int(interval),
                    "date": str(date),
                    # "time": 0,
                    "noofdays": 1
                },
                "svcGroup": "portfolio",
                "svcVersion": "1.0.0",
                "svcName": "jhistorical_New",
                "requestType": "U"
            }
        }

        headers = {"Authorization": str(self.session_token), "charset": "utf-8", "Content-Type": "application/json"}

        last_exception = None
        for attempt in range(1, max_retries + 1):
            if self.is_base64:
                body = self.json_to_base64(params)
                resp = req.post(url_ohlc_data, data=body, headers=headers, verify=self.ssl_verify, timeout=timeout)
                # If API returns base64 encoded payload in text
                resp_obj = self.base64_to_json(resp.text)
            else:
                resp = req.post(url_ohlc_data, json=params, headers=headers, verify=self.ssl_verify, timeout=timeout)
                # raise for 4xx/5xx to handle retriable statuses separately
                resp.raise_for_status()
                resp_obj = resp.json()
            try:
                # Extract the OHLC data path you previously used
                response_data = resp_obj.get('response', {}).get('data', {}).get('data')

            except Exception as e:
                print(f'Error in getting data {e}')
                continue
            # Validate: accept non-empty list-like responses
            if response_data:
                if len(response_data)>5:
                    # Good response found
                    return response_data
                else:
                    print(f'Short Data found for date-->{date},Hence retrying.......')
                    time.sleep(10)
                    continue
            else:
                time.sleep(10)
                continue

    def get_contract_data(self):
        svc_name="getAllContract"
        url_contract_info=self.get_url(svc_name)
        stoken=self.session_token
        headers={ "Authorization":""+str(stoken) ,"charset": "utf-8", "Content-Type": "application/json" }
        all_cont_data=req.request("GET", url_contract_info, headers=headers, verify=self.ssl_verify)
        all_cont_data = all_cont_data.text
        lines = all_cont_data.strip().split('\n')
        headers = lines[0].split(',')
        json_list = []
        for row in lines[1:]:
            values = row.split(',')
            item = dict(zip(headers, values))
            json_list.append(item)
        json_df=pd.DataFrame(json_list)
        return json_df


    def close_connection(self):
        if self.ws_apollo:
            print("Closing connection... and Apollo Heartbeat has been stopped!")
            self.hb_stop_event.set()
            self.on_close(ws=self.ws_apollo,close_status_code="Error_Code:0",close_msg="")

