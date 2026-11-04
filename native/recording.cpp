// Windows system-only recording acceleration. Each handle belongs to its worker thread.
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <d3d11.h>
#include <dxgi1_2.h>
#include <mfapi.h>
#include <mfidl.h>
#include <mfreadwrite.h>
#include <d3dcompiler.h>
#include <algorithm>
#include <cstring>
#include <new>

template<class T> struct Ref {
    T* p=nullptr;
    ~Ref(){if(p)p->Release();}
    T** put(){if(p)p->Release();p=nullptr;return &p;}
    T* operator->()const{return p;}
    operator T*()const{return p;}
};
#define TRY(x) do {HRESULT result=(x);if(FAILED(result))return result;}while(0)
struct Device {
    Ref<ID3D11Device> device;
    Ref<ID3D11DeviceContext> context;
    HRESULT init(IDXGIAdapter* adapter=nullptr){
        UINT flags=D3D11_CREATE_DEVICE_BGRA_SUPPORT|D3D11_CREATE_DEVICE_VIDEO_SUPPORT;
        return D3D11CreateDevice(adapter,adapter?D3D_DRIVER_TYPE_UNKNOWN:D3D_DRIVER_TYPE_HARDWARE,
            nullptr,flags,nullptr,0,D3D11_SDK_VERSION,device.put(),nullptr,context.put());
    }
};
struct Capture:Device {
    Ref<IDXGIOutputDuplication> duplication;
    Ref<ID3D11Texture2D> cached,staging;
    RECT bounds={};UINT width=0,height=0;bool valid=false;
    HRESULT init(int left,int top,int w,int h){
        Ref<IDXGIFactory1> factory;TRY(CreateDXGIFactory1(__uuidof(IDXGIFactory1),(void**)factory.put()));
        for(UINT a=0;;++a){
            Ref<IDXGIAdapter1> adapter;
            if(factory->EnumAdapters1(a,adapter.put())==DXGI_ERROR_NOT_FOUND)break;
            for(UINT o=0;;++o){
                Ref<IDXGIOutput> output;
                if(adapter->EnumOutputs(o,output.put())==DXGI_ERROR_NOT_FOUND)break;
                DXGI_OUTPUT_DESC desc={};TRY(output->GetDesc(&desc));
                RECT r=desc.DesktopCoordinates;
                if(!desc.AttachedToDesktop||left<r.left||top<r.top||left+w>r.right||top+h>r.bottom)continue;
                // Rotation uses the GDI fallback, rather than recording a wrongly rotated surface.
                if(desc.Rotation!=DXGI_MODE_ROTATION_IDENTITY)return DXGI_ERROR_UNSUPPORTED;
                TRY(Device::init(adapter));
                Ref<IDXGIOutput1> output1;TRY(output->QueryInterface(__uuidof(IDXGIOutput1),(void**)output1.put()));
                TRY(output1->DuplicateOutput(device,duplication.put()));bounds=r;return S_OK;
            }
        }
        return DXGI_ERROR_NOT_FOUND;
    }
    HRESULT acquire(){
        DXGI_OUTDUPL_FRAME_INFO info={};Ref<IDXGIResource> resource;
        HRESULT hr=duplication->AcquireNextFrame(valid?0:100,&info,resource.put());
        if(SUCCEEDED(hr)){
            Ref<ID3D11Texture2D> texture;
            hr=resource->QueryInterface(__uuidof(ID3D11Texture2D),(void**)texture.put());
            if(SUCCEEDED(hr)){
                if(!cached.p){
                    D3D11_TEXTURE2D_DESC d={};texture->GetDesc(&d);
                    d.Usage=D3D11_USAGE_DEFAULT;d.BindFlags=D3D11_BIND_RENDER_TARGET;d.CPUAccessFlags=0;d.MiscFlags=0;
                    hr=device->CreateTexture2D(&d,nullptr,cached.put());
                }
                if(SUCCEEDED(hr)){context->CopyResource(cached,texture);valid=true;}
            }
            duplication->ReleaseFrame();
            if(FAILED(hr))return hr;
        }else if(hr!=DXGI_ERROR_WAIT_TIMEOUT)return hr;
        if(!valid)return DXGI_ERROR_WAIT_TIMEOUT;
        return S_OK;
    }
    HRESULT grab(int left,int top,int w,int h,void* dest,int stride){
        if(w<=0||h<=0||stride<w*4||left<bounds.left||top<bounds.top||left+w>bounds.right||top+h>bounds.bottom)return E_INVALIDARG;
        if(width!=(UINT)w||height!=(UINT)h){
            D3D11_TEXTURE2D_DESC d={};d.Width=w;d.Height=h;d.MipLevels=1;d.ArraySize=1;
            d.Format=DXGI_FORMAT_B8G8R8A8_UNORM;d.SampleDesc.Count=1;d.Usage=D3D11_USAGE_STAGING;d.CPUAccessFlags=D3D11_CPU_ACCESS_READ;
            TRY(device->CreateTexture2D(&d,nullptr,staging.put()));width=w;height=h;
        }
        TRY(acquire());
        D3D11_BOX box={(UINT)(left-bounds.left),(UINT)(top-bounds.top),0,(UINT)(left-bounds.left+w),(UINT)(top-bounds.top+h),1};
        context->CopySubresourceRegion(staging,0,0,0,0,cached,0,&box);
        D3D11_MAPPED_SUBRESOURCE map={};TRY(context->Map(staging,0,D3D11_MAP_READ,0,&map));
        for(int y=0;y<h;++y)memcpy((BYTE*)dest+y*stride,(BYTE*)map.pData+y*map.RowPitch,w*4);
        context->Unmap(staging,0);return S_OK;
    }
};
struct Encoder:Device {
    Ref<ID3D11VideoDevice> video;
    Ref<ID3D11VideoContext> videoContext;
    Ref<ID3D11VideoProcessorEnumerator> enumerator;
    Ref<ID3D11VideoProcessor> processor;
    Ref<ID3D11Texture2D> input;
    Ref<ID3D11VideoProcessorInputView> inputView;
    Ref<IMFDXGIDeviceManager> manager;
    Ref<IMFSinkWriter> writer;
    Ref<ID3D11VertexShader> vertexShader;
    Ref<ID3D11PixelShader> pixelShader;
    Ref<ID3D11SamplerState> sampler;
    Ref<ID3D11BlendState> blend;
    Ref<ID3D11Buffer> constants;
    Ref<ID3D11RenderTargetView> renderTarget;
    UINT width=0,height=0,fps=0;DWORD stream=0;bool mf=false,co=false,started=false;
    ~Encoder(){finish();}
    HRESULT finish(){
        HRESULT result=S_OK;
        if(writer.p){if(started)result=writer->Finalize();writer.put();}
        // Release all MF objects before shutdown; release COM in the owning thread.
        manager.put();
        if(mf){MFShutdown();mf=false;}
        if(co){CoUninitialize();co=false;}
        return result;
    }
    HRESULT init(const wchar_t* path,UINT w,UINT h,UINT rate,UINT bitrate,Device* shared=nullptr){
        width=w;height=h;fps=rate;
        HRESULT initResult=CoInitializeEx(nullptr,COINIT_MULTITHREADED);
        if(FAILED(initResult)&&initResult!=RPC_E_CHANGED_MODE)return initResult;
        co=SUCCEEDED(initResult);TRY(MFStartup(MF_VERSION));mf=true;
        if(shared){device.p=shared->device;device->AddRef();context.p=shared->context;context->AddRef();}
        else{TRY(Device::init());}
        Ref<ID3D10Multithread> multi;
        if(SUCCEEDED(device->QueryInterface(__uuidof(ID3D10Multithread),(void**)multi.put())))multi->SetMultithreadProtected(TRUE);
        TRY(device->QueryInterface(__uuidof(ID3D11VideoDevice),(void**)video.put()));
        TRY(context->QueryInterface(__uuidof(ID3D11VideoContext),(void**)videoContext.put()));
        D3D11_VIDEO_PROCESSOR_CONTENT_DESC content={};content.InputFrameFormat=D3D11_VIDEO_FRAME_FORMAT_PROGRESSIVE;
        content.InputFrameRate={rate,1};content.OutputFrameRate={rate,1};content.InputWidth=w;content.InputHeight=h;
        content.OutputWidth=w;content.OutputHeight=h;content.Usage=D3D11_VIDEO_USAGE_PLAYBACK_NORMAL;
        TRY(video->CreateVideoProcessorEnumerator(&content,enumerator.put()));
        TRY(video->CreateVideoProcessor(enumerator,0,processor.put()));
        UINT support=0;TRY(enumerator->CheckVideoProcessorFormat(DXGI_FORMAT_NV12,&support));
        if(!(support&D3D11_VIDEO_PROCESSOR_FORMAT_SUPPORT_OUTPUT))return E_NOTIMPL;
        D3D11_TEXTURE2D_DESC d={};d.Width=w;d.Height=h;d.MipLevels=1;d.ArraySize=1;
        d.Format=DXGI_FORMAT_B8G8R8A8_UNORM;d.SampleDesc.Count=1;d.Usage=D3D11_USAGE_DEFAULT;d.BindFlags=D3D11_BIND_RENDER_TARGET|D3D11_BIND_SHADER_RESOURCE;
        TRY(device->CreateTexture2D(&d,nullptr,input.put()));
        D3D11_VIDEO_PROCESSOR_INPUT_VIEW_DESC view={};view.ViewDimension=D3D11_VPIV_DIMENSION_TEXTURE2D;
        TRY(video->CreateVideoProcessorInputView(input,enumerator,&view,inputView.put()));
        RECT rect={0,0,(LONG)w,(LONG)h};videoContext->VideoProcessorSetStreamSourceRect(processor,0,TRUE,&rect);
        videoContext->VideoProcessorSetStreamDestRect(processor,0,TRUE,&rect);
        videoContext->VideoProcessorSetOutputTargetRect(processor,TRUE,&rect);
        videoContext->VideoProcessorSetStreamAutoProcessingMode(processor,0,FALSE);
        D3D11_VIDEO_PROCESSOR_COLOR_SPACE rgb={};rgb.RGB_Range=0;rgb.YCbCr_Matrix=0;rgb.Nominal_Range=2;
        D3D11_VIDEO_PROCESSOR_COLOR_SPACE yuv={};yuv.YCbCr_Matrix=0;yuv.Nominal_Range=1;
        videoContext->VideoProcessorSetStreamColorSpace(processor,0,&rgb);
        videoContext->VideoProcessorSetOutputColorSpace(processor,&yuv);
        UINT token=0;TRY(MFCreateDXGIDeviceManager(&token,manager.put()));TRY(manager->ResetDevice(device,token));
        Ref<IMFAttributes> attrs;TRY(MFCreateAttributes(attrs.put(),3));
        TRY(attrs->SetUINT32(MF_READWRITE_ENABLE_HARDWARE_TRANSFORMS,TRUE));
        TRY(attrs->SetUINT32(MF_LOW_LATENCY,TRUE));
        TRY(attrs->SetUnknown(MF_SINK_WRITER_D3D_MANAGER,manager));
        TRY(MFCreateSinkWriterFromURL(path,nullptr,attrs,writer.put()));
        Ref<IMFMediaType> output;TRY(MFCreateMediaType(output.put()));
        TRY(output->SetGUID(MF_MT_MAJOR_TYPE,MFMediaType_Video));TRY(output->SetGUID(MF_MT_SUBTYPE,MFVideoFormat_H264));
        TRY(output->SetUINT32(MF_MT_AVG_BITRATE,bitrate));TRY(output->SetUINT32(MF_MT_INTERLACE_MODE,MFVideoInterlace_Progressive));
        TRY(MFSetAttributeSize(output,MF_MT_FRAME_SIZE,w,h));TRY(MFSetAttributeRatio(output,MF_MT_FRAME_RATE,rate,1));
        TRY(MFSetAttributeRatio(output,MF_MT_PIXEL_ASPECT_RATIO,1,1));TRY(writer->AddStream(output,&stream));
        Ref<IMFMediaType> in;TRY(MFCreateMediaType(in.put()));
        TRY(in->SetGUID(MF_MT_MAJOR_TYPE,MFMediaType_Video));TRY(in->SetGUID(MF_MT_SUBTYPE,MFVideoFormat_NV12));
        TRY(in->SetUINT32(MF_MT_INTERLACE_MODE,MFVideoInterlace_Progressive));
        TRY(MFSetAttributeSize(in,MF_MT_FRAME_SIZE,w,h));TRY(MFSetAttributeRatio(in,MF_MT_FRAME_RATE,rate,1));
        TRY(MFSetAttributeRatio(in,MF_MT_PIXEL_ASPECT_RATIO,1,1));TRY(writer->SetInputMediaType(stream,in,nullptr));
        TRY(writer->BeginWriting());started=true;return S_OK;
    }
    HRESULT write(const void* bgra,UINT stride,LONGLONG timestamp,LONGLONG duration){
        if(!started||stride<width*4||timestamp<0||duration<=0)return E_INVALIDARG;
        context->UpdateSubresource(input,0,nullptr,bgra,stride,0);
        return commit(timestamp,duration);
    }
    HRESULT commit(LONGLONG timestamp,LONGLONG duration){
        // A fresh surface is retained by the sample until the encoder releases it.
        // Reusing one NV12 surface while MF still owns it would corrupt earlier frames.
        Ref<ID3D11Texture2D> nv12;D3D11_TEXTURE2D_DESC d={};d.Width=width;d.Height=height;d.MipLevels=1;d.ArraySize=1;
        d.Format=DXGI_FORMAT_NV12;d.SampleDesc.Count=1;d.Usage=D3D11_USAGE_DEFAULT;d.BindFlags=D3D11_BIND_RENDER_TARGET;
        TRY(device->CreateTexture2D(&d,nullptr,nv12.put()));
        Ref<ID3D11VideoProcessorOutputView> view;D3D11_VIDEO_PROCESSOR_OUTPUT_VIEW_DESC vd={};vd.ViewDimension=D3D11_VPOV_DIMENSION_TEXTURE2D;
        TRY(video->CreateVideoProcessorOutputView(nv12,enumerator,&vd,view.put()));
        D3D11_VIDEO_PROCESSOR_STREAM streamData={};streamData.Enable=TRUE;streamData.pInputSurface=inputView;
        TRY(videoContext->VideoProcessorBlt(processor,view,0,1,&streamData));
        context->Flush();
        Ref<IMFMediaBuffer> buffer;TRY(MFCreateDXGISurfaceBuffer(__uuidof(ID3D11Texture2D),nv12,0,FALSE,buffer.put()));
        DWORD length=0;TRY(buffer->GetMaxLength(&length));TRY(buffer->SetCurrentLength(length));
        Ref<IMFSample> sample;TRY(MFCreateSample(sample.put()));TRY(sample->AddBuffer(buffer));
        TRY(sample->SetSampleTime(timestamp));TRY(sample->SetSampleDuration(duration));
        return writer->WriteSample(stream,sample);
    }
    HRESULT initOverlay(){
        if(vertexShader.p)return S_OK;
        const char* source=
            "cbuffer R:register(b0){float4 rect;}"
            "struct V{float4 pos:SV_POSITION;float2 uv:TEXCOORD0;};"
            "V vs(uint id:SV_VertexID){V v;float2 uv=float2(id&1,id>>1);v.pos=float4(rect.xy+uv*rect.zw,0,1);v.uv=uv;return v;}"
            "Texture2D image:register(t0);SamplerState smp:register(s0);"
            "float4 ps(V v):SV_TARGET{return image.Sample(smp,v.uv);}"
            ;
        Ref<ID3DBlob> vs,ps,error;
        TRY(D3DCompile(source,strlen(source),nullptr,nullptr,nullptr,"vs","vs_4_0",D3DCOMPILE_OPTIMIZATION_LEVEL3,0,vs.put(),error.put()));
        TRY(D3DCompile(source,strlen(source),nullptr,nullptr,nullptr,"ps","ps_4_0",D3DCOMPILE_OPTIMIZATION_LEVEL3,0,ps.put(),error.put()));
        TRY(device->CreateVertexShader(vs->GetBufferPointer(),vs->GetBufferSize(),nullptr,vertexShader.put()));
        TRY(device->CreatePixelShader(ps->GetBufferPointer(),ps->GetBufferSize(),nullptr,pixelShader.put()));
        D3D11_SAMPLER_DESC sd={};sd.Filter=D3D11_FILTER_MIN_MAG_MIP_LINEAR;sd.AddressU=sd.AddressV=sd.AddressW=D3D11_TEXTURE_ADDRESS_CLAMP;
        TRY(device->CreateSamplerState(&sd,sampler.put()));
        D3D11_BLEND_DESC bd={};bd.RenderTarget[0].BlendEnable=TRUE;
        bd.RenderTarget[0].SrcBlend=D3D11_BLEND_SRC_ALPHA;bd.RenderTarget[0].DestBlend=D3D11_BLEND_INV_SRC_ALPHA;
        bd.RenderTarget[0].BlendOp=D3D11_BLEND_OP_ADD;bd.RenderTarget[0].SrcBlendAlpha=D3D11_BLEND_ONE;
        bd.RenderTarget[0].DestBlendAlpha=D3D11_BLEND_INV_SRC_ALPHA;bd.RenderTarget[0].BlendOpAlpha=D3D11_BLEND_OP_ADD;
        bd.RenderTarget[0].RenderTargetWriteMask=D3D11_COLOR_WRITE_ENABLE_ALL;
        TRY(device->CreateBlendState(&bd,blend.put()));
        D3D11_BUFFER_DESC cb={};cb.ByteWidth=16;cb.Usage=D3D11_USAGE_DEFAULT;cb.BindFlags=D3D11_BIND_CONSTANT_BUFFER;
        TRY(device->CreateBuffer(&cb,nullptr,constants.put()));
        TRY(device->CreateRenderTargetView(input,nullptr,renderTarget.put()));return S_OK;
    }
    HRESULT overlay(const void* pixels,int w,int h,int stride,float x,float y,float dw,float dh){
        if(!pixels||w<=0||h<=0||stride<w*4)return E_INVALIDARG;
        TRY(initOverlay());
        D3D11_TEXTURE2D_DESC d={};d.Width=w;d.Height=h;d.MipLevels=1;d.ArraySize=1;d.Format=DXGI_FORMAT_B8G8R8A8_UNORM;
        d.SampleDesc.Count=1;d.Usage=D3D11_USAGE_IMMUTABLE;d.BindFlags=D3D11_BIND_SHADER_RESOURCE;
        D3D11_SUBRESOURCE_DATA data={pixels,(UINT)stride,0};Ref<ID3D11Texture2D> texture;
        TRY(device->CreateTexture2D(&d,&data,texture.put()));Ref<ID3D11ShaderResourceView> resource;
        TRY(device->CreateShaderResourceView(texture,nullptr,resource.put()));
        float rect[]={2*x/width-1,1-2*y/height,2*dw/width,-2*dh/height};
        context->UpdateSubresource(constants,0,nullptr,rect,0,0);
        D3D11_VIEWPORT viewport={0,0,(float)width,(float)height,0,1};context->RSSetViewports(1,&viewport);
        ID3D11RenderTargetView* target=renderTarget;context->OMSetRenderTargets(1,&target,nullptr);
        context->OMSetBlendState(blend,nullptr,0xffffffff);
        context->IASetInputLayout(nullptr);context->IASetPrimitiveTopology(D3D11_PRIMITIVE_TOPOLOGY_TRIANGLESTRIP);
        context->VSSetShader(vertexShader,nullptr,0);context->PSSetShader(pixelShader,nullptr,0);
        ID3D11Buffer* cb=constants;context->VSSetConstantBuffers(0,1,&cb);
        ID3D11ShaderResourceView* srv=resource;context->PSSetShaderResources(0,1,&srv);
        ID3D11SamplerState* ss=sampler;context->PSSetSamplers(0,1,&ss);context->Draw(4,0);
        srv=nullptr;context->PSSetShaderResources(0,1,&srv);context->OMSetRenderTargets(0,nullptr,nullptr);return S_OK;
    }
};
struct Recorder {
    Capture capture;Encoder encoder;
    Ref<ID3D11VideoProcessorEnumerator> scaleEnum;
    Ref<ID3D11VideoProcessor> scaleProcessor;
    Ref<ID3D11VideoProcessorOutputView> scaleTarget;
    Ref<ID3D11VideoProcessorInputView> scaleSource;
    HRESULT prepare(int left,int top,int w,int h){
        RECT b=capture.bounds;
        if(w<=0||h<=0||left<b.left||top<b.top||left+w>b.right||top+h>b.bottom)return E_INVALIDARG;
        TRY(capture.acquire());
        if(w==(int)encoder.width&&h==(int)encoder.height){
            D3D11_BOX box={(UINT)(left-b.left),(UINT)(top-b.top),0,(UINT)(left-b.left+w),(UINT)(top-b.top+h),1};
            capture.context->CopySubresourceRegion(encoder.input,0,0,0,0,capture.cached,0,&box);return S_OK;
        }
        if(!scaleEnum.p){
            D3D11_VIDEO_PROCESSOR_CONTENT_DESC c={};c.InputFrameFormat=D3D11_VIDEO_FRAME_FORMAT_PROGRESSIVE;
            c.InputWidth=b.right-b.left;c.InputHeight=b.bottom-b.top;c.OutputWidth=encoder.width;c.OutputHeight=encoder.height;
            c.InputFrameRate={encoder.fps,1};c.OutputFrameRate={encoder.fps,1};c.Usage=D3D11_VIDEO_USAGE_PLAYBACK_NORMAL;
            TRY(encoder.video->CreateVideoProcessorEnumerator(&c,scaleEnum.put()));
            TRY(encoder.video->CreateVideoProcessor(scaleEnum,0,scaleProcessor.put()));
            D3D11_VIDEO_PROCESSOR_INPUT_VIEW_DESC iv={};iv.ViewDimension=D3D11_VPIV_DIMENSION_TEXTURE2D;
            TRY(encoder.video->CreateVideoProcessorInputView(capture.cached,scaleEnum,&iv,scaleSource.put()));
            D3D11_VIDEO_PROCESSOR_OUTPUT_VIEW_DESC ov={};ov.ViewDimension=D3D11_VPOV_DIMENSION_TEXTURE2D;
            TRY(encoder.video->CreateVideoProcessorOutputView(encoder.input,scaleEnum,&ov,scaleTarget.put()));
        }
        RECT source={left-b.left,top-b.top,left-b.left+w,top-b.top+h};RECT target={0,0,(LONG)encoder.width,(LONG)encoder.height};
        encoder.videoContext->VideoProcessorSetStreamSourceRect(scaleProcessor,0,TRUE,&source);
        encoder.videoContext->VideoProcessorSetStreamDestRect(scaleProcessor,0,TRUE,&target);
        encoder.videoContext->VideoProcessorSetOutputTargetRect(scaleProcessor,TRUE,&target);
        D3D11_VIDEO_PROCESSOR_STREAM s={};s.Enable=TRUE;s.pInputSurface=scaleSource;
        return encoder.videoContext->VideoProcessorBlt(scaleProcessor,scaleTarget,0,1,&s);
    }
};
extern "C" {
__declspec(dllexport) HRESULT jj_capture_open(int left,int top,int w,int h,Capture** handle){
    if(!handle||w<=0||h<=0)return E_INVALIDARG;*handle=nullptr;
    Capture* p=new(std::nothrow) Capture;if(!p)return E_OUTOFMEMORY;
    HRESULT hr=p->init(left,top,w,h);if(FAILED(hr)){delete p;return hr;}*handle=p;return S_OK;
}
__declspec(dllexport) HRESULT jj_capture_grab(Capture* p,int left,int top,int w,int h,void* dest,int stride){
    return p&&dest?p->grab(left,top,w,h,dest,stride):E_INVALIDARG;
}
__declspec(dllexport) void jj_capture_close(Capture* p){delete p;}
__declspec(dllexport) HRESULT jj_encoder_open(const wchar_t* path,int w,int h,int fps,int bitrate,Encoder** handle){
    if(!path||!handle||w<16||h<16||(w&1)||(h&1)||fps<=0||bitrate<=0)return E_INVALIDARG;*handle=nullptr;
    Encoder* p=new(std::nothrow) Encoder;if(!p)return E_OUTOFMEMORY;
    HRESULT hr=p->init(path,w,h,fps,bitrate);if(FAILED(hr)){delete p;return hr;}*handle=p;return S_OK;
}
__declspec(dllexport) HRESULT jj_encoder_write(Encoder* p,const void* data,int stride,LONGLONG time,LONGLONG duration){
    return p&&data?p->write(data,stride,time,duration):E_INVALIDARG;
}
__declspec(dllexport) HRESULT jj_encoder_close(Encoder* p){if(!p)return S_OK;HRESULT hr=p->finish();delete p;return hr;}
__declspec(dllexport) HRESULT jj_recorder_open(const wchar_t* path,int left,int top,int mw,int mh,int w,int h,int fps,int bitrate,Recorder** handle){
    if(!handle||!path||w<16||h<16||(w&1)||(h&1)||fps<=0||bitrate<=0)return E_INVALIDARG;*handle=nullptr;
    Recorder* p=new(std::nothrow) Recorder;if(!p)return E_OUTOFMEMORY;
    HRESULT hr=p->capture.init(left,top,mw,mh);
    if(SUCCEEDED(hr))hr=p->encoder.init(path,w,h,fps,bitrate,&p->capture);
    if(FAILED(hr)){delete p;return hr;}*handle=p;return S_OK;
}
__declspec(dllexport) HRESULT jj_recorder_prepare(Recorder* p,int x,int y,int w,int h){return p?p->prepare(x,y,w,h):E_INVALIDARG;}
__declspec(dllexport) HRESULT jj_recorder_overlay(Recorder* p,const void* data,int w,int h,int stride,float x,float y,float dw,float dh){
    return p?p->encoder.overlay(data,w,h,stride,x,y,dw,dh):E_INVALIDARG;
}
__declspec(dllexport) HRESULT jj_recorder_commit(Recorder* p,LONGLONG ts,LONGLONG duration){return p?p->encoder.commit(ts,duration):E_INVALIDARG;}
__declspec(dllexport) HRESULT jj_recorder_close(Recorder* p){if(!p)return S_OK;HRESULT hr=p->encoder.finish();delete p;return hr;}
}
