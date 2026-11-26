#!/usr/bin/env bash
set -Eeuo pipefail
. /etc/os-release
[[ $ID == ubuntu && $(dpkg --print-architecture) == amd64 ]] || { echo "Ubuntu amd64 required"; exit 1; }
case "${1:-}" in
--test)
  [[ $EUID != 0 ]] || { echo "Run tests as your desktop user, not root"; exit 1; }
  lspci -nnk | grep -A3 -E 'VGA compatible controller|3D controller|Display controller'
  echo "Checking AMD GPU kernel drivers"
  if [[ -n ${DISPLAY:-} ]]; then glxinfo -B || true; fi
  vulkaninfo --summary
  # Small real compute test, with a checked result on each physical GPU.
  scratch=$(mktemp -d)
  trap 'rm -rf "$scratch"' EXIT
  cat > "$scratch/test.comp" <<'EOF'
#version 450
layout(local_size_x=1) in;
layout(set=0,binding=0,std430) buffer Result { uint value; } r;
void main(){ r.value=123u*456u; }
EOF
  glslangValidator -V "$scratch/test.comp" -o "$scratch/test.spv"
  cat > "$scratch/test.c" <<'EOF'
#include <vulkan/vulkan.h>
#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#define CK(x) do { VkResult r=(x); if(r!=VK_SUCCESS){fprintf(stderr,"%s: %d\n",#x,r);exit(1);} }while(0)
int main(int argc,char **argv){
 if(argc!=2)return 1;
 FILE *f=fopen(argv[1],"rb"); if(!f)return 1;
 uint32_t code[4096]; size_t bytes=fread(code,1,sizeof(code),f); fclose(f);
 if(!bytes||bytes==sizeof(code)||bytes%4)return 1;
 VkInstance inst; CK(vkCreateInstance(&(VkInstanceCreateInfo){.sType=VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO},0,&inst));
 uint32_t n=0; CK(vkEnumeratePhysicalDevices(inst,&n,0)); if(!n)return 1;
 VkPhysicalDevice *p=calloc(n,sizeof(*p)); CK(vkEnumeratePhysicalDevices(inst,&n,p));
 unsigned seen=0;
 for(uint32_t i=0;i<n;i++){
 VkPhysicalDeviceProperties prop; vkGetPhysicalDeviceProperties(p[i],&prop);
 if(prop.deviceType==VK_PHYSICAL_DEVICE_TYPE_CPU || prop.vendorID!=0x1002)continue;
 printf("Testing %s\n",prop.deviceName); fflush(stdout);
 uint32_t qn=0; vkGetPhysicalDeviceQueueFamilyProperties(p[i],&qn,0);
 VkQueueFamilyProperties *qs=calloc(qn,sizeof(*qs)); vkGetPhysicalDeviceQueueFamilyProperties(p[i],&qn,qs);
 uint32_t q=0; while(q<qn && !(qs[q].queueFlags&VK_QUEUE_COMPUTE_BIT))q++; free(qs); if(q==qn)return 1;
 float priority=1; VkDeviceQueueCreateInfo qi={.sType=VK_STRUCTURE_TYPE_DEVICE_QUEUE_CREATE_INFO,.queueFamilyIndex=q,.queueCount=1,.pQueuePriorities=&priority};
 VkDevice d; CK(vkCreateDevice(p[i],&(VkDeviceCreateInfo){.sType=VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO,.queueCreateInfoCount=1,.pQueueCreateInfos=&qi},0,&d));
 VkQueue queue; vkGetDeviceQueue(d,q,0,&queue);
 VkBuffer b; CK(vkCreateBuffer(d,&(VkBufferCreateInfo){.sType=VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO,.size=4,.usage=VK_BUFFER_USAGE_STORAGE_BUFFER_BIT},0,&b));
 VkMemoryRequirements req; vkGetBufferMemoryRequirements(d,b,&req);
 VkPhysicalDeviceMemoryProperties mp; vkGetPhysicalDeviceMemoryProperties(p[i],&mp);
 uint32_t m=0; for(;m<mp.memoryTypeCount;m++)if((req.memoryTypeBits&(1u<<m))&&(mp.memoryTypes[m].propertyFlags&6)==6)break;
 if(m==mp.memoryTypeCount)return 1;
 VkDeviceMemory mem; CK(vkAllocateMemory(d,&(VkMemoryAllocateInfo){.sType=VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO,.allocationSize=req.size,.memoryTypeIndex=m},0,&mem));
 CK(vkBindBufferMemory(d,b,mem,0)); void *ptr; CK(vkMapMemory(d,mem,0,4,0,&ptr)); *(uint32_t*)ptr=0;
 VkDescriptorSetLayoutBinding bind={.binding=0,.descriptorType=VK_DESCRIPTOR_TYPE_STORAGE_BUFFER,.descriptorCount=1,.stageFlags=VK_SHADER_STAGE_COMPUTE_BIT};
 VkDescriptorSetLayout sl; CK(vkCreateDescriptorSetLayout(d,&(VkDescriptorSetLayoutCreateInfo){.sType=VK_STRUCTURE_TYPE_DESCRIPTOR_SET_LAYOUT_CREATE_INFO,.bindingCount=1,.pBindings=&bind},0,&sl));
 VkPipelineLayout pl; CK(vkCreatePipelineLayout(d,&(VkPipelineLayoutCreateInfo){.sType=VK_STRUCTURE_TYPE_PIPELINE_LAYOUT_CREATE_INFO,.setLayoutCount=1,.pSetLayouts=&sl},0,&pl));
 VkShaderModule sm; CK(vkCreateShaderModule(d,&(VkShaderModuleCreateInfo){.sType=VK_STRUCTURE_TYPE_SHADER_MODULE_CREATE_INFO,.codeSize=bytes,.pCode=code},0,&sm));
 VkPipeline pipe; CK(vkCreateComputePipelines(d,0,1,&(VkComputePipelineCreateInfo){.sType=VK_STRUCTURE_TYPE_COMPUTE_PIPELINE_CREATE_INFO,.layout=pl,.stage={.sType=VK_STRUCTURE_TYPE_PIPELINE_SHADER_STAGE_CREATE_INFO,.stage=VK_SHADER_STAGE_COMPUTE_BIT,.module=sm,.pName="main"}},0,&pipe));
 VkDescriptorPoolSize ps={VK_DESCRIPTOR_TYPE_STORAGE_BUFFER,1}; VkDescriptorPool pool; CK(vkCreateDescriptorPool(d,&(VkDescriptorPoolCreateInfo){.sType=VK_STRUCTURE_TYPE_DESCRIPTOR_POOL_CREATE_INFO,.maxSets=1,.poolSizeCount=1,.pPoolSizes=&ps},0,&pool));
 VkDescriptorSet ds; CK(vkAllocateDescriptorSets(d,&(VkDescriptorSetAllocateInfo){.sType=VK_STRUCTURE_TYPE_DESCRIPTOR_SET_ALLOCATE_INFO,.descriptorPool=pool,.descriptorSetCount=1,.pSetLayouts=&sl},&ds));
 VkDescriptorBufferInfo bi={b,0,4}; vkUpdateDescriptorSets(d,1,&(VkWriteDescriptorSet){.sType=VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET,.dstSet=ds,.descriptorCount=1,.descriptorType=VK_DESCRIPTOR_TYPE_STORAGE_BUFFER,.pBufferInfo=&bi},0,0);
 VkCommandPool cp; CK(vkCreateCommandPool(d,&(VkCommandPoolCreateInfo){.sType=VK_STRUCTURE_TYPE_COMMAND_POOL_CREATE_INFO,.queueFamilyIndex=q},0,&cp));
 VkCommandBuffer cb; CK(vkAllocateCommandBuffers(d,&(VkCommandBufferAllocateInfo){.sType=VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO,.commandPool=cp,.commandBufferCount=1},&cb));
 CK(vkBeginCommandBuffer(cb,&(VkCommandBufferBeginInfo){.sType=VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO}));
 vkCmdBindPipeline(cb,VK_PIPELINE_BIND_POINT_COMPUTE,pipe); vkCmdBindDescriptorSets(cb,VK_PIPELINE_BIND_POINT_COMPUTE,pl,0,1,&ds,0,0);
 vkCmdDispatch(cb,1,1,1);
 VkMemoryBarrier barrier={.sType=VK_STRUCTURE_TYPE_MEMORY_BARRIER,.srcAccessMask=VK_ACCESS_SHADER_WRITE_BIT,.dstAccessMask=VK_ACCESS_HOST_READ_BIT};
 vkCmdPipelineBarrier(cb,VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT,VK_PIPELINE_STAGE_HOST_BIT,0,1,&barrier,0,0,0,0);
 CK(vkEndCommandBuffer(cb)); CK(vkQueueSubmit(queue,1,&(VkSubmitInfo){.sType=VK_STRUCTURE_TYPE_SUBMIT_INFO,.commandBufferCount=1,.pCommandBuffers=&cb},0)); CK(vkQueueWaitIdle(queue));
 if(*(uint32_t*)ptr!=56088u){fprintf(stderr,"Incorrect compute result\n");return 1;}
 printf("PASS: %s (56088)\n",prop.deviceName);
 seen++;
 vkUnmapMemory(d,mem); vkDestroyCommandPool(d,cp,0); vkDestroyDescriptorPool(d,pool,0); vkDestroyPipeline(d,pipe,0); vkDestroyShaderModule(d,sm,0); vkDestroyPipelineLayout(d,pl,0); vkDestroyDescriptorSetLayout(d,sl,0); vkDestroyBuffer(d,b,0); vkFreeMemory(d,mem,0); vkDestroyDevice(d,0);
 }
 free(p); vkDestroyInstance(inst,0); if(seen<2){fprintf(stderr,"Two physical AMD GPUs must pass\n");return 1;} puts("PASS: both AMD GPUs executed Vulkan compute"); return 0;
}
EOF
  cc -std=c99 -O2 "$scratch/test.c" -lvulkan -o "$scratch/test"
  timeout 45s "$scratch/test" "$scratch/test.spv"
  ;;
"")
  [[ $EUID == 0 ]] || { echo "Run with sudo bash"; exit 1; }
  [[ -d /run/systemd/system ]] || exit 1
  apt-get -o APT::Update::Error-Mode=any update
  apt-get install -y --no-remove ca-certificates wget pciutils linux-firmware libgl1-mesa-dri mesa-vulkan-drivers mesa-utils vulkan-tools libvulkan-dev glslang-tools build-essential lm-sensors
  tmp=$(mktemp -d)
  trap 'rm -rf "$tmp"' EXIT
  chmod 755 "$tmp"
  wget -O "$tmp/chrome.deb" https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb
  chmod 644 "$tmp/chrome.deb"
  apt-get install -y --no-remove "$tmp/chrome.deb"
  # Hawaii (CIK) needs amdgpu instead of the legacy radeon driver.
  if lspci -nn | grep -Ei "VGA|3D controller|Display controller" | grep -Eiq "1002:67b[0-9a-f]"; then
    if [[ -f /etc/default/grub ]]; then
      install -d /etc/default/grub.d
      printf '%s\n' 'GRUB_CMDLINE_LINUX_DEFAULT="$GRUB_CMDLINE_LINUX_DEFAULT radeon.cik_support=0 amdgpu.cik_support=1"' > /etc/default/grub.d/99-sirus-amd-cik.cfg
      update-grub
    else
      echo "Non-GRUB bootloader: manually add radeon.cik_support=0 amdgpu.cik_support=1"
    fi
  fi
  echo "Installation complete. Reboot manually, then run bash install-workstation.sh --test as your regular user."
  ;;
*) echo "Usage: sudo bash install-workstation.sh OR bash install-workstation.sh --test"; exit 2;;
esac
