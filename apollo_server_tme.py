from greek_api_duplicate import GreekAPI
api=GreekAPI(user='G911',s_pwd='greek@123',pwd='g@6666666666',procli='2',ac_no='',is_secure=False,is_base_64=True,rest_ip='dev.greeksoft.in',rest_port='3333',iris=True)

token_list=[]
api.start_apollo(token_list,req_data='allresp')

for data in api.data_stream_fast():
    print(data)